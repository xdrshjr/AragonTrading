# Agent 行情体制打标签工具 `label_market_regime` —— 设计方案 (spec)

> 文档版本：**v2**（Subtask #1 评审与修复后 —— 全部 P0/P1 已就地修复，详见下方「评审记录」；评审结论见文末）
> Feature slug：`agent-market-regime-labeling`
> 作者：Solution Architect（Anthropic Eng）；评审：Senior Reviewer（Anthropic Eng）
> 关联全局目标：为 Agent Trading Copilot 增加「**对行情先思考、再分类打标签、再判断该用什么策略**」的工具能力与配套**渐进式提示词**。
> 前置依赖：`docs/plans/agent-trading-copilot/spec.md`（v2，已落地的 Agent Copilot 工具体系）。

---

## 0. 评审记录（Review Notes · Subtask #1）

> 评审方式：逐节对照**真实源码**核验四个维度——可行性（Feasibility）/ 完备性（Completeness）/ 一致性（Consistency）/ 适度性（Right-sizing）。
> 已核验文件（均为本仓真实路径，行号截至评审时）：`agent/loop.py`、`agent/tools/base.py`、`agent/tools/registry.py`、`agent/tools/strategy_tools.py`、`agent/services.py`、`agent/prompts.py`、`engine/indicators.py`、`engine/backtester.py`、`engine/__init__.py`、`engine/simulator.py`、`okx/types.py`、`okx/rest_client.py`、`strategies/__init__.py`、`strategies/base.py`、`db/repositories.py`、`db/session.py`、`tests/conftest.py`、`tests/test_agent_tools.py`。
> 严重度：**P0**=阻断/会致错，必须改；**P1**=会误导实现或埋下功能缺陷，必须改；**P2**=建议项，实现期注意。**所有 P0/P1 已在 v2 正文就地修复**（每条标「✅ 已修复」并注明落点）。

### P0
- 无。核心集成面（summary 回喂、`ToolRegistry` 注册与异常收敛、`candles_to_df`、`load_range_candles`、指标纯函数、`Candle.confirm`、8 个策略 key）经核对均与真实源码吻合，方案整体可行。

### P1（必须修复，已就地修复）

- **P1-1｜`payload` 数据流误述（一致性 / 可行性）。** §4.3 / §2.1 / §2.6 / §4.2 称结构化 `payload`「仅前端可选渲染」。但据 `agent/loop.py:390-394`：对**非 ui-action** 工具，WS `tool_result` 事件只带 `{id,name,ok,summary}`；`payload` **仅当 `is_ui_action=True`** 时才作为 `ui_action` 事件下发。`label_market_regime` 是普通工具（`is_ui_action=False`），其 `payload` **既不回喂模型、也不下发前端**，当前无任何线上消费方。
  ✅ **已修复**：§2.1 / §4.2 / §4.3 / §2.6 改为如实说明——回喂模型与前端展示的**唯一通道是 `summary`**；`payload` 仅供**单元测试断言结构**与**未来接线**（接线需改 loop/WS，明确列为本期范围外），前端工具卡渲染自 `summary`，不依赖 `payload`。

- **P1-2｜`validate` 示例签名缺 `args`（可行性 / 一致性）。** §4.1 写 `require_str("instId")`、`check_enum("bar",BAR_ENUM,required=True)`，缺首个位置参数 `args`。据 `agent/tools/base.py:103/111` 与既有用法 `agent/tools/strategy_tools.py:67-74`，真实签名为 `require_str(args, key)`、`check_enum(args, key, allowed, *, required=False)`。
  ✅ **已修复**：§4.1 `validate` 示例改为 args 在先的真实签名。

- **P1-3｜`STRATEGY_REGISTRY` 注册副作用 / 导入来源（可行性 / 一致性）。** `candidate_strategies()` 依赖 `STRATEGY_REGISTRY` 已被填充，而填充发生在导入 **`strategies` 包**（`strategies/__init__.py` 逐个 import 触发 `@register_strategy`）之时；仅导入 `strategies.base` 得到的是**空 dict**。§2.1 / §5 把真源标注为 `strategies/base.py · STRATEGY_REGISTRY`，纯引擎单测若只 import `engine.regime` 会拿到空注册表 → `candidate_strategies` 返回 `[]`（假性通过 / 候选为空）。
  ✅ **已修复**：§2.1 / §5.1 / §5.2 明确 `engine/regime.py` 经 **包导入** `from ..strategies import STRATEGY_REGISTRY`（与既有 `db/repositories.py:15`、`db/session.py:12` 一致）以保证注册副作用；§3.1 / §6.1 注明 `test_engine_regime.py` 须 `import app.strategies`；并校正「纯计算/不触 IO」措辞为「不触网络/DB/磁盘 IO；仅依赖 `engine.indicators` 与 `strategies` 注册表元数据，与既有 `engine/backtester.py:12` 同样向 `strategies` 取依赖、无环（`engine/__init__` 为薄包、`simulator` 为叶子）」。

- **P1-4｜snapshot 取数未过滤未确认（forming）K 线（完备性 / 一致性，触 C1/R3）。** snapshot 模式无区间，须用 `get_rest_client().get_candles(inst_id,bar,limit)` 直取最新 N 根（`load_range_candles` 强制要求区间）。但 `get_candles` 返回的**最后一根可能是未收盘的 forming bar**（`confirm=False`，见 `okx/types.py:24` 与 `agent/services.py:27` 的 `confirm` 过滤）。若以其作 `close[n-1]`，将用到未完成数据，违反因果（R3）与「不假数据」（C1）。
  ✅ **已修复**：§2.3 取数段与 §8 增加硬约束——snapshot 路径取数后**先剔除尾部未确认根**（`confirm` 过滤，复用与 `_filter_range_candles` 相同语义），再以剩余末根为 `n-1`。

### P2（建议项；其中 a–c 已顺手就地收紧，d–e 留作实现期条件，见文末结论）

- **P2-a｜`scores["breakout"]` 公式残缺。** §2.3 该式以 `...` 结尾且引用未定义的 `breakDistanceATR`。✅ 已就地改为完整、可由 `evidence` 推导的表达式。
- **P2-b｜timeline 缺 `startTs/endTs` 行为未定义。** `load_range_candles` 强制区间，模型可能不带区间调用 timeline。✅ 已在 §2.4 / §4.1 注明：缺区间时回退「取最新 `limit` 根」（走 `get_candles`，同 snapshot 取数与 confirm 过滤）。
- **P2-c｜可用根数 < `baseline_win` 时分位基线未述。** ✅ 已在 §2.3 注明：分位在「实际可得根数」上计算（仍以 `min_bars` 为下限拒绝）。
- **P2-d｜`evidence` 字段在 §4.2 与 §5.1 间不完全对齐**（`lastTs/lastClose`）。建议实现期以 §5.1 `RegimeEvidence` 为准统一序列化。
- **P2-e｜R5 模型遵从度仅能在真实模型下验证。** 建议实现期补一条 network 冒烟（核对真实模型是否照三步脚手架显式推理），离线单测无法覆盖该面。

---

## 1. Overview（概述）

本方案为已落地的 **Agent Trading Copilot**（右侧「AI 助手」对话面板，后端 agentic loop + 工具调用）新增**一套行情体制（market regime）识别能力**：核心是一个新工具 `label_market_regime`，配合一套**渐进式提示词（progressive prompts）**，让 Agent 在回答「现在是什么行情 / 该用哪种策略 / 某策略到底在哪种市场里赚钱」这类问题时，遵循「**先思考（思维链）→ 再分类打标签 → 再映射策略**」的纪律化推理路径，而不是凭直觉一句话带过。

工具本身做的是**确定性、可复现、可审计**的量化特征计算：从真实 OKX K 线出发，复用项目既有的纯函数技术指标（`engine/indicators.py` 的 EMA / SMA / RSI / Bollinger / ATR），在**严格因果（不使用未来数据）**的前提下，沿三条正交轴度量当前窗口——**结构轴**（趋势上 / 趋势下 / 区间）、**波动轴**（高波动 / 低波动）、**事件轴**（急跌 crash / 突破 breakout）——产出 7 个标签的打分、初判标签集合、底层证据数字，以及一份「按体制推荐的候选策略」清单。模型拿到这份**带证据的初判**后，并不直接复述，而是被工具结果里附带的**分阶段推理脚手架**牵引，显式写出三段式思考，给出最终标签与策略建议。

这一「工具算证据 + 提示词管推理」的分工是本方案的核心取舍：**指标数字必须由确定性引擎给出**（与项目「绝不假数据 / 不臆造指标」的硬约束一致，C1），**分类与策略判断则交给模型在受控脚手架下完成**（满足任务「需要让 Agent 先思考、思维链思考，然后进行分类打标签，并思考该用什么策略」的诉求）。工具进一步支持 `timeline` 模式，把一段较长区间切成若干**因果体制子段**，正是为了支撑任务里强调的顶级用法——「之后所有策略结果都按 regime 拆开看，不只看总收益，而问它到底在哪种市场里赚钱」：Agent 可先对一段历史做体制时间线，再把某次回测（`run_backtest` 拿到的 `runId` + `get_run_trades` 的成交）按体制子段归并解读，回答「这套策略在趋势里赚、在震荡里亏」这类结构化结论。

整套能力是既有工具体系的**薄增量**：新增 1 个工具类 + 1 个纯计算引擎模块 + 提示词增量 + 注册一行，**不改任何对外 WS/REST 契约、不动数据库、不引入三方依赖**，与 `agent-trading-copilot` 既定架构、`CLAUDE.md` 代码阈值、以及「密钥只在后端 / 工具是既有能力的薄包装」诸约定完全一致。

---

## 2. Technical design（技术设计）

### 2.1 在既有架构中的位置

```text
AgentSession.run (agent/loop.py)
  └─ _stream_assistant_turn → LLM 决定调用 label_market_regime
       └─ _execute_tool_calls → ToolRegistry.execute("label_market_regime", args, ctx)
            └─ LabelMarketRegimeTool.execute(args, ctx)         [新] agent/tools/regime_tools.py
                 ├─ 取真实 K 线：services.load_range_candles / get_rest_client().get_candles
                 ├─ candles_to_df()                              复用 engine/backtester.py
                 ├─ classify_regime(df, bar) / segment_regime()  [新] engine/regime.py（纯计算）
                 │     └─ ema/sma/rsi/bollinger/atr               复用 engine/indicators.py
                 └─ 组装 ToolResult(summary=证据+初判+渐进式脚手架, payload=结构化体制数据)
       └─ loop 把 result.summary 作为 tool_result 回喂模型（本工具为非 ui-action：payload 既不回喂模型、也不下发前端，仅供单测/未来接线，P1-1）
       └─ 下一轮：模型按脚手架显式三段式推理 → 文本输出最终标签 + 策略建议
```

**关键事实（已对照真实源码核验，构成可行性基线）：**

- `agent/loop.py:390-394` —— `tool_result` 事件仅发 `{id,name,ok,summary}`，且 `ToolResultBlock(content=result.summary, ...)` 回喂模型的也是 `summary`；**`payload` 仅当 `is_ui_action=True` 时**才作为 `ui_action` 事件经 WS 发前端。本工具非 ui-action，故 **`payload` 无任何线上消费方**，**渐进式 CoT 脚手架与证据数字必须全部写进 `summary`**——这是本方案最关键的落点（P1-1）。
- `engine/indicators.py` —— `ema/sma/rsi/bollinger/atr` 均为**纯函数、因果（trailing 窗口）、不就地改入参**，可直接复用算体制特征。
- `engine/backtester.py:26` —— `candles_to_df(candles)` 把 `Candle` 列表转成含 `ts,o,h,l,c,vol` 列、按 `ts` 升序的 DataFrame，可直接喂指标函数。
- `okx/types.py · Candle` —— 字段 `ts(秒级 UTC),o,h,l,c,vol,confirm`；`okx/rest_client.py · get_candles(inst_id,bar,limit,*,before,after)` 返回**升序**列表，单次上限 300。
- `agent/services.py · load_range_candles(rest,*,inst_id,bar,limit,start_ts,end_ts)` —— 拉区间真实 K 线并过滤未确认 / 越界，空区间抛 `HTTPException(400, EMPTY_RANGE)`（registry 统一收敛为 `ok=False`）。
- `agent/tools/base.py · Tool` —— 子类设类属性 `name/description/input_schema/is_ui_action` + 实现 `validate/execute`；`base.py` 已备 `require_str/check_enum/check_optional_range/check_required_range/check_limit/first_error` 校验原语可复用。
- `agent/tools/registry.py:27` —— `_ALL_TOOLS` 列表注册全部工具实例；`schemas()` 出 LLM 工具表，`execute()` 统一异常收敛 + 入参 `validate`。
- `strategies/__init__.py · BUILTIN_ORDER` + 注册副作用 / `strategies/base.py · STRATEGY_REGISTRY` —— 8 个内置策略 key（`long_shadow_reversal`/`momentum_breakout`/`ema_cross_scalper`/`bollinger_reversion`/`vwap_rsi_momentum`/`trend_pullback_long`/`trend_pullback_short`/`range_scalping_strategy`，已与本方案映射逐一核对）为唯一真源。**注意（P1-3）**：`STRATEGY_REGISTRY` 由导入 **`strategies` 包**（`__init__.py` 逐个 import 触发 `@register_strategy`）填充；仅 import `strategies.base` 得到空 dict。故 `engine/regime.py` 须 `from ..strategies import STRATEGY_REGISTRY`（包导入，与 `db/repositories.py:15`、`db/session.py:12` 一致），据此过滤体制→策略映射，保证永不指向不存在的策略。
- `agent/prompts.py · SYSTEM_PROMPT / build_system_prompt(ui)` —— 系统提示集中此处；可注入体制能力 primer。
- `agent/llm/loop` 已支持 `thinking_delta` 流式事件（`loop.py:351`）。但因 KIMI/Anthropic 标准的「思考」需开扩展思考模式不保证可用，本方案的脚手架**要求模型把推理以可见正文显式写出**，从而**不依赖 provider 原生思考**也能保证思维链。

### 2.2 体制分类法（taxonomy）—— 三正交轴模型

把 7 个标签归到三条正交轴，避免「既高波动又低波动」式自相矛盾，且让打分与决策互不耦合：

| 轴 | 取值（互斥） | 含义 |
|---|---|---|
| **结构轴**（必有其一） | `trend_up` / `trend_down` / `range` | 价格的方向性结构：上行趋势 / 下行趋势 / 水平区间 |
| **波动轴**（0 或 1） | `high_volatility` / `low_volatility` /（normal） | 当前真实波幅相对近端基线的分位 |
| **事件轴**（0 或 1，瞬态） | `crash` / `breakout` /（none） | 叠加在结构之上的剧烈瞬态：急跌瀑布 / 区间突破 |

最终 `labels` = `[结构标签]` + 命中的波动标签 + 命中的事件标签，例如 `["trend_down","high_volatility","crash"]` 或 `["range","low_volatility"]`。该分类法是 `engine/regime.py · REGIME_LABELS`（标签→定义文本）的**唯一真源**，被「系统提示 primer」与「工具结果脚手架」共同引用，杜绝两处定义漂移（C6）。

### 2.3 确定性特征与判定（engine/regime.py，纯计算、严格因果）

设回看窗口末根索引为 `n-1`，全部特征只用 `≤ n-1` 的数据（因果，无未来泄漏，与项目回测约定一致）。默认参数见 `RegimeParams`（§5.1），均**相对量纲**（分位 / 比率 / ATR 倍数），故跨标的、跨周期稳健，不含绝对价位魔法数。

**取数与基线**：拉 `max(limit, baseline_win)` 根（默认 snapshot 取 200、timeline 取 300，硬上限 300=OKX 单页）。
- **snapshot 取数（P1-4）**：无区间，用 `get_rest_client().get_candles(inst_id, bar, limit)` 直取最新 N 根（升序）；**取回后先剔除尾部未确认（`confirm=False`）的 forming bar**（语义同 `agent/services.py:27 _filter_range_candles`），再以剩余末根为 `n-1`，杜绝用未完成数据（因果 R3 / 不假数据 C1）。
- **timeline 取数**：给了 `startTs/endTs` 则用 `services.load_range_candles`（自带越界+未确认过滤、空区间 400）；**缺区间时回退**取最新 `limit` 根（同 snapshot 路径与 confirm 过滤，P2-b）。
- 回看窗口 `W = trend_win`（默认 30）。基线窗口 `baseline_win`（默认 150）用于波动分位；**当实际可得根数 < `baseline_win` 时，分位在「实际可得根数」上计算**（仍以 `min_bars` 为硬下限拒绝，P2-c）。

**① 趋势 / 方向（结构轴）**
- 方向效率比（Kaufman Efficiency Ratio，越接近 1 越「单边」、越接近 0 越「来回」）：
  `ER = |close[n-1] - close[n-1-W]| / Σ_{i=n-W}^{n-1} |close[i] - close[i-1]|`（分母为 0 时 ER=0）。
- 净变化：`netChange = (close[n-1] - close[n-1-W]) / close[n-1-W]`。
- 均线：`ema_fast = ema(close, ema_fast_p=20)`、`ema_slow = ema(close, ema_slow_p=50)`；`emaSlopePct = (ema_slow[n-1] - ema_slow[n-1-W]) / ema_slow[n-1-W]`；`align_up = ema_fast[n-1] ≥ ema_slow[n-1]`（`align_dn` 反之）。
- 中轴占比：`fracAbove = (W 根内 close > ema_slow 的根数) / W`（趋势时趋近 0/1，区间时趋近 0.5）。
- **判定**：`er ≥ er_trend_min(0.35)` 且 `netChange>0` 且 `align_up` ⇒ `trend_up`；`er ≥ er_trend_min` 且 `netChange<0` 且 `align_dn` ⇒ `trend_down`；否则 ⇒ `range`。

**② 波动（波动轴）**
- `atrPct = atr(df, atr_period=14)[n-1] / close[n-1]`。
- `atrPctRank` = `atrPct[n-1]` 在 `baseline_win` 根 `atrPct` 序列里的分位（≤ 当前值的比例 ∈[0,1]）。
- 布林带宽：`(upper,ma,lower)=bollinger(close,20,2)`；`bbWidth = (upper-lower)/ma`，`bbWidthRank` 同法取分位（用于「低波动收口 / 突破前挤压」佐证）。
- **判定**：`atrPctRank ≥ vol_high_pct(0.80)` ⇒ `high_volatility`；`≤ vol_low_pct(0.20)` ⇒ `low_volatility`；否则 `normal`。

**③ 急跌 crash（事件轴）**
- 短窗跌幅：`fastDrop = (min(close[n-crash_win:n]) - close[n-1-crash_win]) / close[n-1-crash_win]`（`crash_win=12`）。
- `rsiNow = rsi(close,14)[n-1]`。
- **判定（合取，降低误报）**：`fastDrop ≤ crash_drop(-0.08)` 且 `atrPctRank ≥ crash_vol_pct(0.85)` 且 `rsiNow < 35` ⇒ `crash`。

**④ 突破 breakout（事件轴）**
- 前期区间端点（**排除当前根**，因果）：`priorHigh = max(high[n-1-W:n-1])`、`priorLow = min(low[n-1-W:n-1])`。
- 放量比：`volRatio = vol[n-1] / mean(vol[n-1-W:n-1])`。
- **判定**：`close[n-1] > priorHigh + breakout_atr_k(0.25)*atr[n-1]` 且 `volRatio ≥ breakout_vol_mult(1.5)` ⇒ 向上 `breakout`；对称地跌破 `priorLow` 同条件 ⇒ 向下 `breakout`。事件轴 `crash` 与 `breakout` 同时满足时，以 `crash`（更紧迫的风险事件）优先。

**打分 `scores[label] ∈ [0,1]`（用于透明展示 + 作为模型的「初判假设」，并非最终结论）**：
- `trendConf = clamp((er - er_trend_min) / (0.85 - er_trend_min), 0, 1)`；`scores["trend_up"]=trendConf if (netChange>0 and align_up) else 0`；`scores["trend_down"]=trendConf if (netChange<0 and align_dn) else 0`。
- `scores["range"] = clamp((er_trend_min - er)/er_trend_min, 0, 1) * (1 - 2*|fracAbove-0.5|)`。
- `scores["high_volatility"]=clamp((atrPctRank - vol_high_pct)/(1 - vol_high_pct),0,1)`；`scores["low_volatility"]=clamp((vol_low_pct - atrPctRank)/vol_low_pct,0,1)`。
- `scores["crash"]`：命中阈值则 `clamp(fastDrop/crash_drop,0,1)`（跌得越深越高），否则 0。
- `scores["breakout"]`：命中则 `clamp(breakDistATR, 0, 1) * clamp((volRatio - 1)/(breakout_vol_mult - 1), 0, 1)`，否则 0；其中 `breakDistATR = |close[n-1] - edge| / atr[n-1]`，`edge` 为突破方向对应端点（上破取 `priorHigh`、下破取 `priorLow`）——破得越远、放量越足则越高（P2-a，全部由 `evidence` 数字推导）。
- `confidence = scores[primary]`（结构轴主标签得分）。

> 设计意图：`primary/volatility/event` 三个**离散判定**来自上面的显式阈值规则（可单测、可复现）；`scores` 是与之一致的连续置信度（argmax(结构子集) 必等于 `primary`）。两者一起进 `summary`，模型据「初判 + 证据」做最终裁决，可在脚手架引导下**带理由地修正**（如证据矛盾时下调置信、或叠加判定时取舍）。

### 2.4 `timeline` 模式（支撑「按 regime 拆开看」）

`segment_regime(df, bar, params, *, max_segments=12, min_seg_bars=8)`：在窗口上**因果滚动**——从第一个可计算根（`≥ max(ema_slow_p, atr_period, baseline 起算)`）起，对每根用「只含该根及之前」的子序列调用 `classify_regime` 的结构判定，得到逐根 `primary`，再把相邻同 `primary` 的根**合并为子段** `{startTs,endTs,primary,labels,bars,confidence}`；过短子段（`< min_seg_bars`）并入相邻主段，最终至多 `max_segments` 段（超出按时长合并最短段，并在 summary 注明「已合并」）。**严格因果**：每段标签只用该段结束前的数据，无前视。Agent 拿到子段时间线后，可把 `get_run_trades` 的成交按 `ts` 落到对应子段，得到「分体制盈亏」口径（由模型在对话里完成归并，无需新工具，符合既有「Agent 组合既有工具」哲学）。

### 2.5 渐进式提示词（progressive prompts）—— 两层

任务的「渐进式」落为**两层、由泛到精、即时升格**的提示，二者文本同源引用 §2.2 的 `REGIME_LABELS`：

**层一 · 系统提示 primer（常驻、低specificity）** —— 写入 `agent/prompts.py · SYSTEM_PROMPT`：
- 在「可用工具」列表追加：`label_market_regime：对当前或指定区间的行情做体制识别（趋势上/下、区间、高/低波动、急跌、突破），返回带证据的初判与候选策略；支持 snapshot/timeline 两种模式。`
- 新增一条纪律（编号接既有 1–5）：「6. **遇到「现在是什么行情 / 该用什么策略 / 某段在什么市场里赚钱」类问题**：先调用 `label_market_regime` 取确定性证据，再按工具返回的三步脚手架**显式写出思维链**（① 判结构与波动与事件 ② 给最终标签 ③ 映射并取舍策略），最后才给结论；**不要凭空臆造指标或跳过推理**。」
- 附 7 标签一句话定义（取自 `REGIME_LABELS`，保证模型未调用工具时也理解词义）。

**层二 · 工具结果脚手架（即时、高specificity）** —— `LabelMarketRegimeTool.execute` 把 `engine/regime.py` 算出的证据 + 初判，拼上 `agent/prompts.py · REGIME_REASONING_SCAFFOLD` 常量，作为 `summary` 回喂模型。脚手架文本（节选骨架）：

```text
【证据已就绪 · 请按三步显式推理后再下结论】
第①步 思考（思维链）：逐轴研判——
  - 结构：ER={er}、净变化={netChange}、EMA{fast}/{slow} 关系={align}、中轴上方占比={fracAbove}，是趋势还是区间？方向？
  - 波动：ATR%={atrPct}（分位 {atrPctRank}）、带宽分位={bbWidthRank}，处于高/低/常态？
  - 事件：短窗跌幅={fastDrop}、RSI={rsi}、是否破前高/前低（{priorHigh}/{priorLow}，放量 {volRatio}×）？有无 crash/breakout？
  对每一轴写出你的判断与依据；若与下方“确定性初判”冲突，请说明你为何采信/修正。
第②步 打标签：给出最终 labels（结构 1 个 + 可选波动 + 可选事件）及各自置信与一句理由。
第③步 选策略：从“候选策略”出发，结合本体制说明各策略为何适配/不适配、需注意什么（如高波动放宽止损），给出 1–2 个推荐；必要时建议用 run_backtest 验证或用 timeline 把表现按体制拆开看。
——确定性初判（仅供参考，非最终）：primary={primary}，volatility={vol}，event={event}，候选策略={candidates}。
```

> 这样「渐进式」体现在：层一在系统层面**常驻**地建立「先思考再打标签再选策略」的范式；层二在模型**恰好拿到证据的那一刻**注入高细节的分步指令，把抽象纪律升格为可照做的清单——即「按需即时升格」的 progressive prompting。因脚手架要求模型**把推理写进可见正文**，故无论 provider 是否支持原生 thinking，思维链都被强制产出。

### 2.6 关键时序（用户：「现在 BTC 永续是什么行情？该用哪个策略？」）

```text
AgentPanel.send ─WS→ AgentSession.run
  → LLM(round1) 依 primer 决定调用 label_market_regime{instId:BTC-USDT-SWAP,bar:1H}
  → tool_call 事件（前端卡片：📍市场体制分析）
  → execute：取真实近端 200 根 → candles_to_df → classify_regime → 组装 summary(证据+初判+脚手架)
  → tool_result 回喂模型
  → LLM(round2) 按脚手架显式写出 ①逐轴思考 ②最终标签(如 trend_up + high_volatility) ③策略取舍
       （可能再调用 list_strategies 校准 id，或 run_backtest 验证）→ message_end → done
```

---

## 3. File / module change plan（文件 / 模块变更计划）

> 约定：新建标 **[新]**、修改标 **[改]**。后端 PEP8 + Google docstring，单文件 ≤800 行、单函数 ≤50 行、形参 ≤5、圈复杂度 ≤10、嵌套 ≤4（CLAUDE.md 阈值）。前端 camelCase。

### 3.1 后端（services/quant）—— 必做

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| `app/engine/regime.py` | [新] | 体制计算引擎：`REGIME_LABELS`/`REGIME_STRATEGY_HINTS`/`RegimeParams`/`RegimeEvidence`/`RegimeResult`/`RegimeSegment` + `classify_regime()`/`segment_regime()`/`candidate_strategies()`。严格因果、**不触网络/DB/磁盘 IO**；除 `engine/indicators.py` 外，经**包导入** `from ..strategies import STRATEGY_REGISTRY` 取注册表元数据（与既有 `engine/backtester.py:12` 同样向 `strategies` 取依赖、无环：`engine/__init__` 薄包、`simulator` 为叶子，P1-3） |
| `app/agent/tools/regime_tools.py` | [新] | `LabelMarketRegimeTool`（name=`label_market_regime`）：schema/validate/execute；取真实 K 线→`candles_to_df`→`engine/regime`→组装含脚手架的 summary + 结构化 payload |
| `app/agent/tools/registry.py` | [改] | import `LabelMarketRegimeTool` 并加入 `_ALL_TOOLS`（一行注册） |
| `app/agent/prompts.py` | [改] | `SYSTEM_PROMPT` 追加工具说明 + 第 6 条纪律 + 7 标签定义；新增 `REGIME_REASONING_SCAFFOLD` 常量（供工具拼 summary） |

> 拆分理由：体制特征计算放 `engine/`（与 `indicators.py/backtester.py` 同层，纯计算、可被 UI/其它工具复用、利于单测）；工具壳放 `agent/tools/`（薄包装 + 取数 + 脚手架拼装）。`REGIME_LABELS`/`REGIME_STRATEGY_HINTS` 作单一真源置于 `engine/regime.py`，`prompts.py` 与工具均 import，杜绝定义漂移。

### 3.2 前端（apps/web）—— 可选增强（不阻塞验收）

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| `components/Agent/ToolCallCard.tsx` | [改·可选] | `TOOL_LABELS` 增 `label_market_regime: { label: "市场体制分析", icon: "📍" }`（仅美化工具卡标题；不加即回退通用 🔧+name 渲染，功能不受影响） |

> 既有 `ToolCallCard` 对未知工具有通用渲染（`labelFor` 回退 `{label:name,icon:"🔧"}`），故**前端零改动亦可正常工作**；本项纯属体验镀金，列为可选。无新增组件、无 store/类型/reducer 改动。

### 3.3 测试

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| `services/quant/tests/test_engine_regime.py` | [新] | 纯引擎单测：合成序列覆盖 trend_up/down、range、high/low_vol、crash、breakout 各自判定 + 因果性 + `segment_regime` 合并语义 + `candidate_strategies` 只回注册表内 key。**首行 `import app.strategies` 以填充 `STRATEGY_REGISTRY`（P1-3，否则 `candidate_strategies` 取空注册表假性通过）** |
| `services/quant/tests/test_agent_regime_tool.py` | [新] | 工具级单测：假 OKX（复用 `test_agent_tools.py · _FakeRest` 范式，monkeypatch `app.agent.tools.regime_tools.get_rest_client`）→ summary 含证据+脚手架三步+候选策略；**末根 `confirm=False` 被剔除、不进入判定（P1-4）**；空/不可达→`ok=False`（C1）；`validate` 正反例 |

### 3.4 不涉及

- **数据库**：无新表、无迁移（体制标签随 `tool_result.summary` 已被既有 `AgentMessage` 持久化，无需独立存储）。
- **三方依赖**：`numpy/pandas` 已是回测既有依赖；零新增。
- **WS/REST 契约**：工具经既有 `ToolRegistry` 暴露，不新增端点、不改信封。
- **桌面打包**：纯后端 Python 增量，随既有 PyInstaller 打包，无需改 `desktop/`。

---

## 4. Interface design（接口设计）

### 4.1 工具 JSON Schema（`label_market_regime`）

```json
{
  "name": "label_market_regime",
  "description": "对某标的某周期的当前或指定区间行情做体制识别：返回趋势上/下、区间、高/低波动、急跌、突破等标签的打分与带证据初判，以及按体制推荐的候选策略；mode=timeline 时把区间切成因果体制子段，便于把策略表现按体制拆开看。",
  "input_schema": {
    "type": "object",
    "properties": {
      "instId": {"type": "string", "description": "如 BTC-USDT-SWAP / ETH-USDT"},
      "bar": {"type": "string", "enum": ["1m","5m","15m","30m","1H","4H","1D"]},
      "mode": {"type": "string", "enum": ["snapshot","timeline"], "description": "snapshot=最新窗口单点判定（默认）；timeline=区间切体制子段"},
      "startTs": {"type": "integer", "description": "秒级 UTC 起（可选；timeline 建议给，snapshot 缺省取最新）"},
      "endTs": {"type": "integer", "description": "秒级 UTC 止（可选）"},
      "limit": {"type": "integer", "description": "取数根数（snapshot 默认 200 / timeline 默认 300，硬上限 300）"}
    },
    "required": ["instId","bar"]
  }
}
```

`validate`（复用 `agent/tools/base.py` 原语，**均为 args 在先的真实签名**，对齐 `agent/tools/strategy_tools.py:67-74` 用法，P1-2）：

```python
def validate(self, args: dict) -> str | None:
    return first_error(
        require_str(args, "instId"),
        check_enum(args, "bar", BAR_ENUM, required=True),   # BAR_ENUM 取自 base.py（元组）
        check_enum(args, "mode", ("snapshot", "timeline")),  # 可选，缺省按 snapshot
        check_optional_range(args),                          # startTs/endTs 可选且有序
        check_limit(args, 1, 300),                           # 可选 limit，硬上限 300
    )
```

### 4.2 `ToolResult.summary`（回喂模型 —— 决定推理质量的关键面）

纯中文文本，结构 = **证据数字摘要** + **确定性初判** + **§2.5 三步脚手架**。snapshot 例（数字示意）：

```text
BTC-USDT-SWAP 1H 体制研判（近 200 根，截至 1718000000）：
证据：ER=0.52、净变化=+6.8%、EMA20>EMA50(多头排列)、中轴上方占比=0.78；
      ATR%=1.9%（近端分位 0.86=高）、带宽分位 0.71；短窗跌幅=-1.2%、RSI=63；
      前高=71500/前低=66800，最新放量 1.3×。
确定性初判（仅供参考，非最终）：primary=trend_up，volatility=high_volatility，event=none；
候选策略：trend_pullback_long / ema_cross_scalper / momentum_breakout。
【请按三步显式推理后再下结论】第①步 思考：……第②步 打标签：……第③步 选策略：……
```

timeline 例：逐段 `子段k: [startTs~endTs] primary=range (低波动), 32 根` 列表 + 「已合并最短段」提示 + 同款三步脚手架（要求模型按段解读、并提示可与 `get_run_trades` 对齐做分体制盈亏）。

### 4.3 `ToolResult.payload`（结构化数据：仅供单测断言 / 未来接线，当前不回喂模型、也不下发前端；camelCase）

```jsonc
// snapshot
{
  "mode": "snapshot", "instId": "BTC-USDT-SWAP", "bar": "1H", "lastTs": 1718000000,
  "primary": "trend_up", "volatility": "high_volatility", "event": "none",
  "labels": ["trend_up","high_volatility"], "confidence": 0.71,
  "scores": {"trend_up":0.71,"trend_down":0,"range":0.12,"high_volatility":0.66,
             "low_volatility":0,"crash":0,"breakout":0},
  "evidence": {"er":0.52,"netChange":0.068,"emaSlopePct":0.041,"fracAbove":0.78,
               "atrPct":0.019,"atrPctRank":0.86,"bbWidth":0.052,"bbWidthRank":0.71,
               "rsi":63.0,"fastDrop":-0.012,"priorHigh":71500,"priorLow":66800,
               "volRatio":1.3,"bars":200},
  "candidateStrategies": [{"key":"trend_pullback_long","name":"趋势回调做多"}, ...]
}
// timeline：上述顶层去掉 scores/evidence，新增
// "segments": [{"startTs":..,"endTs":..,"primary":"range","labels":["range","low_volatility"],"bars":32,"confidence":0.4}, ...],
// "segmentCount": 7, "merged": true
```

> **数据流真相（P1-1）**：本工具非 ui-action，故 `payload` **当前既不回喂模型、也不经 WS 下发前端**（`agent/loop.py:390-394`：`tool_result` 事件仅带 `summary`，`payload` 只对 `is_ui_action` 工具作 `ui_action` 下发）。`ToolCallCard` 工具卡只渲染 `summary` 文本，**不依赖 payload**。payload 在本期的**唯一消费方是单元测试**（断言 `segments`/结构正确）；若未来要做「体制色带叠加左图」，需另改 loop/WS 把该 payload 下发前端，属本期范围外。

---

## 5. Data model（数据模型）

### 5.1 内存形状（`engine/regime.py`，无持久化）

```python
REGIME_LABELS: dict[str, str]              # 7 标签 → 一句话定义（系统提示 / 脚手架共用真源，C6）
REGIME_STRATEGY_HINTS: dict[str, list[str]]  # 标签 → 候选策略 key 列表（静态数据，见下）
# 运行时按注册表过滤；务必经包导入触发 @register_strategy（P1-3）：
from ..strategies import STRATEGY_REGISTRY   # 包导入 == db/repositories.py:15 的做法，确保注册表非空

@dataclass
class RegimeParams:                         # 全部相对量纲，可被 execute 透传覆盖（默认即生产值）
    trend_win: int = 30; ema_fast_p: int = 20; ema_slow_p: int = 50
    atr_period: int = 14; boll_period: int = 20; rsi_period: int = 14
    baseline_win: int = 150; crash_win: int = 12
    er_trend_min: float = 0.35
    vol_high_pct: float = 0.80; vol_low_pct: float = 0.20
    crash_drop: float = -0.08; crash_vol_pct: float = 0.85
    breakout_atr_k: float = 0.25; breakout_vol_mult: float = 1.5
    min_bars: int = 60                      # 不足即拒（不假数据，C1）

@dataclass
class RegimeEvidence:                       # 全部 float/int，已四舍五入到展示精度
    er: float; netChange: float; emaSlopePct: float; fracAbove: float
    atrPct: float; atrPctRank: float; bbWidth: float; bbWidthRank: float
    rsi: float; fastDrop: float; priorHigh: float; priorLow: float
    volRatio: float; bars: int; lastTs: int; lastClose: float

@dataclass
class RegimeResult:
    primary: str; volatility: str; event: str
    labels: list[str]; scores: dict[str, float]; confidence: float
    evidence: RegimeEvidence; candidateStrategies: list[dict]   # [{"key","name"}]

@dataclass
class RegimeSegment:
    startTs: int; endTs: int; primary: str; labels: list[str]; bars: int; confidence: float
```

`REGIME_STRATEGY_HINTS` 默认映射（key 取自 `BUILTIN_ORDER`，`candidate_strategies()` 运行时过滤掉不在 `STRATEGY_REGISTRY` 的 key、并补 `name`）：

| 体制标签 | 候选策略 key |
|---|---|
| `trend_up` | `trend_pullback_long`, `ema_cross_scalper`, `momentum_breakout` |
| `trend_down` | `trend_pullback_short`, `ema_cross_scalper` |
| `range` | `range_scalping_strategy`, `bollinger_reversion` |
| `high_volatility` | `momentum_breakout`, `vwap_rsi_momentum`（提示放宽止损/降杠杆） |
| `low_volatility` | `range_scalping_strategy`, `bollinger_reversion` |
| `breakout` | `momentum_breakout`, `vwap_rsi_momentum` |
| `crash` | `trend_pullback_short`, `long_shadow_reversal`（仅超跌反抽，强调风险） |

### 5.2 函数签名（`engine/regime.py`）

```python
def classify_regime(df: pd.DataFrame, bar: str, params: RegimeParams | None = None) -> RegimeResult: ...
def segment_regime(df: pd.DataFrame, bar: str, params: RegimeParams | None = None, *,
                   max_segments: int = 12, min_seg_bars: int = 8) -> list[RegimeSegment]: ...
def candidate_strategies(labels: list[str]) -> list[dict]: ...
# 去重、保序；按 STRATEGY_REGISTRY（经包导入填充，P1-3）过滤掉不存在的 key、并以
# STRATEGY_REGISTRY[key].meta.name 补 name → [{"key","name"}]。
```

`df` 为 `candles_to_df` 产物（列 `ts,o,h,l,c,vol`，升序）。`classify_regime` 入口先校验 `len(df) ≥ params.min_bars`，不足抛 `ValueError("数据不足…")`（工具层转 `ok=False`）。

### 5.3 持久化

无新增。体制结论以 `tool_result` 文本进入既有 `AgentMessage`（role=`tool`）持久化，resume / history 重放自动带回，符合 `agent-trading-copilot` §5.1 既定机制。

---

## 6. Testing & acceptance criteria（测试与验收）

### 6.1 单元测试（离线、不依赖真实网络，沿用 pytest）

**`test_engine_regime.py`（纯引擎，合成 K 线 DataFrame）：**
- `test_trend_up_detected`：构造稳定上行（高 ER、多头排列）→ `primary=="trend_up"`、`scores["trend_up"]>scores["range"]`。
- `test_trend_down_detected`：稳定下行 → `primary=="trend_down"`。
- `test_range_detected`：正弦/锯齿水平震荡（低 ER、fracAbove≈0.5）→ `primary=="range"`。
- `test_high_low_volatility_rank`：注入波幅骤升 / 收敛序列 → 末根 `volatility` 为 `high_volatility` / `low_volatility`。
- `test_crash_requires_conjunction`：单纯阴跌（无波动骤升）**不**判 crash；急跌+波动骤升+低 RSI 才判 `crash`（验证合取防误报）。
- `test_breakout_needs_volume_and_break`：放量破前高→`breakout`；破前高但缩量→不判（验证放量门槛）。
- `test_causality_no_lookahead`：对 `df[:k]` 调 `classify_regime` 的 `primary` 等于 `segment_regime(df)` 在覆盖 k 的子段 `primary`（同一根的标签不因未来数据改变）。
- `test_min_bars_guard`：`len(df)<min_bars` → 抛 `ValueError`。
- `test_segment_merge_and_cap`：制造多段交替 → 子段数 ≤ `max_segments`、过短段被并、相邻同 primary 合并。
- `test_candidate_strategies_filtered`：返回 key 全部 ∈ `STRATEGY_REGISTRY`、含 `name`、保序去重；**断言 `STRATEGY_REGISTRY` 非空**（守护 P1-3：测试模块须 `import app.strategies` 触发注册，否则该用例形同虚设）。

**`test_agent_regime_tool.py`（工具壳，假 OKX）：**
- `test_snapshot_summary_has_evidence_and_scaffold`：假 OKX 喂上行 K 线 → `ok=True`，`summary` 含 `ER`/`ATR%`、`primary=trend_up`、候选策略、且含「第①步」「第②步」「第③步」脚手架字样。
- `test_timeline_lists_segments`：`mode=timeline` → `payload["segments"]` 非空、各段 `startTs<endTs`、`summary` 列出子段。
- `test_empty_or_unreachable_not_faked`：空 K 线 / `_FakeRest(raise_okx=True)` → `ok=False`，summary 如实告知（C1，绝不假数据）。
- `test_snapshot_drops_forming_candle`（P1-4）：假 OKX 末根 `confirm=False` → 该 forming bar 不参与判定（`payload.lastTs`/证据末根为倒数第二根的 ts），证明因果取数剔除未收盘根。
- `test_validate_reject_and_accept`：缺 `instId`/越界 `bar`/逆序 `ts`/`limit` 越界 → `validate` 非空；合法入参 → `None`。

### 6.2 验收标准（Definition of Done）

1. `engine/regime.py`、`agent/tools/regime_tools.py` 创建，`label_market_regime` 经 `ToolRegistry().schemas()` 出现在工具表，`registry._ALL_TOOLS` 含其实例。
2. 在 AI 助手输入「现在 BTC 永续是什么行情、该用什么策略」→ Agent 调 `label_market_regime`，并在回复中**显式呈现三段式推理**（先逐轴思考 → 给标签 → 选策略），数字均来自工具证据而非臆造。
3. 输入「把最近这段行情按体制拆开看」→ Agent 调 `mode=timeline`，给出体制子段时间线，并能进一步结合 `run_backtest`/`get_run_trades` 谈「在哪种市场里赚 / 亏」。
4. `engine/regime.py` 全部判定**严格因果**（`test_causality_no_lookahead` 通过）；OKX 不可达 / 数据不足时如实 `ok=False`（C1）。
5. 系统提示含第 6 条纪律与 7 标签定义；工具 summary 含证据 + 初判 + 三步脚手架，且**不依赖 provider 原生 thinking** 即能产出可见思维链。
6. `pnpm test`（后端 pytest 非 network + 前端既有 vitest/tsc）全绿；新增测试全部通过；未破坏既有 `test_agent_*`。
7. 未引入三方依赖、未改 DB / WS / REST 契约、未提交构建产物；候选策略映射只指向 `STRATEGY_REGISTRY` 真实存在的 key。

---

## 7. Risks & mitigations（风险与缓解）

| # | 风险 | 缓解 |
|---|---|---|
| R1 | 阈值因标的 / 周期差异失准（如不同币种波动量级不同） | 全部判据用**相对量纲**（ER、ATR% 分位、ATR 倍数、放量比），不含绝对价位魔法数；阈值集中在 `RegimeParams`（默认即生产值，可透传覆盖）；模型在脚手架引导下可带理由修正确定性初判 |
| R2 | crash / breakout 误报 | 均用**合取条件**（crash=急跌∧波动骤升∧低 RSI；breakout=有效破位∧放量），单测 `test_crash_requires_conjunction`/`test_breakout_needs_volume_and_break` 固化 |
| R3 | 未来数据泄漏（违反项目因果约定） | 全部指标为 trailing 窗口；前高/前低**排除当前根**；`segment_regime` 逐根只用「该根及之前」；`test_causality_no_lookahead` 守护 |
| R4 | summary 过长撑爆上下文 token | 证据只列关键数字并四舍五入；timeline 子段 ≤ `max_segments(12)`、过短合并并注明；脚手架为定长常量 |
| R5 | 模型不照脚手架推理、直接复述初判 | 脚手架显式编号三步并要求「若与初判冲突须说明采信/修正」；系统提示第 6 条强制「先思考再下结论、不得跳过」；可在评审期补一条 network 冒烟核对真实模型遵从度 |
| R6 | 取数根数不足（新上市标的 / 窄区间） | `min_bars` 守卫 + `load_range_candles` 的 `EMPTY_RANGE`；不足即 `ok=False` 如实告知，绝不补零或假数据（C1） |
| R7 | 候选策略映射指向已删策略 | `candidate_strategies()` 运行时按 `STRATEGY_REGISTRY` 过滤并补 `name`，`test_candidate_strategies_filtered` 守护 |
| R8 | `REGIME_LABELS` 在系统提示与脚手架两处漂移 | 单一真源置于 `engine/regime.py`，`prompts.py` 与工具均 import，不复制字面 |
| R9 | 单文件 / 单函数超 CLAUDE.md 阈值 | `classify_regime` 拆 `_trend_features`/`_volatility_features`/`_event_features`/`_score_and_resolve` 辅助函数（各 ≤50 行、嵌套 ≤4）；引擎与工具壳分文件，均 ≪800 行 |
| R10 | OKX 单次取数上限 300 限制基线长度 | `baseline_win` 默认 150 < 300，单页足够；如需更长基线属未来增强（分页），本期不做并在文档注明 |

---

## 8. 附：实现注意事项（给下游工程师）

- **summary 是唯一对外通道（P1-1）**：本工具非 ui-action，`payload` 既不回喂模型、也不下发前端（仅供单测）；脚手架与证据**务必全部进 `summary`**，勿把推理指令或关键数字只放 `payload`。
- **snapshot 必剔除 forming bar（P1-4）**：snapshot 走 `get_rest_client().get_candles(limit)` 取最新 N 根后，**先按 `confirm` 过滤掉未收盘末根**再判定；timeline 给区间走 `load_range_candles`、缺区间回退取最新 `limit` 根（同 confirm 过滤）。
- **注册表用包导入（P1-3）**：`engine/regime.py` 须 `from ..strategies import STRATEGY_REGISTRY`（包导入触发 `@register_strategy`，同 `db/repositories.py:15`）；仅 import `strategies.base` 会得到空注册表。`test_engine_regime.py` 首行 `import app.strategies`。
- **validate 签名 args 在先（P1-2）**：`require_str(args, key)` / `check_enum(args, key, allowed, *, required=)` / `check_limit(args, low, high)`，对齐 `agent/tools/strategy_tools.py` 既有用法。
- **复用而非重写**：指标一律用 `engine/indicators.py`；DataFrame 一律 `candles_to_df`；区间取数优先 `services.load_range_candles`（自带越界过滤 + 空区间 400）。
- **时间秒级 UTC**：入出参 `ts` 全秒级（OKX 适配层已做毫秒转换，勿在工具层重复）。
- **camelCase 边界**：`payload` 字段 camelCase 与前端对齐；`summary` 用中文自然语言。
- **不假数据（C1）**：OKX 不可达 / 数据不足 → `ok=False` + 明确原因，绝不补零、插值或编造指标。
- **因果优先**：任何新增特征都须只用 trailing 数据；新增即补对应 `test_causality_*`。
- **跨平台命令**：建目录用 `python -c "from pathlib import Path; Path('...').mkdir(parents=True, exist_ok=True)"`，勿用 `mkdir -p` / `&&`（见任务环境约定）。
- **提交期精确暂存**：下游 Subtask #3 用 `git add <具体路径>`，严禁 `git add -A`，复核 diff 不含任何密钥 / 构建产物。

（全文 > 800 字，覆盖 Overview / 技术设计 / 文件清单 / 接口 / 数据模型 / 测试验收 / 风险，满足 Definition of Done。）

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2）

> 落码阶段对照 §6.1 测试逐项核验时发现以下需澄清/微调点，均已就地按「修正后做法」实现，不阻塞验收。

- **I-1（澄清 · 因果性的层级，触 §2.4 与 §6.1 `test_causality_no_lookahead` 之张力）。**
  「逐根分类严格因果」成立于**逐根原始结构标签**层（`_per_bar_primaries` / `classify_regime` 的结构判定，只用 ≤ 该根数据）。但 `segment_regime` 的**短段吸收**（§2.4「过短子段并入相邻主段」）会用**相邻段（含未来根）**的上下文去重写被吸收根的**展示用 primary**，属事后去噪，并非该根分类引入了未来数据。故 `classify_regime(df[:k]).primary == segment_regime(df) 覆盖 k 子段的 primary` 这一等式**仅对未被吸收/合并的根严格成立**。
  **修正后做法**：`test_causality_no_lookahead` 在「干净长趋势」数据上断言该等式（此时无短段吸收，等式成立）；no-lookahead 的核心保证（任何根的**原始**分类不依赖未来数据）由 `_per_bar_primaries` 的 trailing-only 计算与 `classify_regime` 共用同一结构判定函数 `_decide_structure` 共同守护，未被削弱。

- **I-2（实现补强 · 非 §5.2 公开清单内的内部辅助）。** 为忠实落地文档既定行为，`engine/regime.py` 另加三个小辅助：`count_structural_runs()`（供工具按 §4.3 诚实判定 timeline 是否真的发生合并：`len(segments) < 原始段数` 即 `merged=True`）、`evidence_to_dict()` / `segment_to_dict()`（以 §5.1 `RegimeEvidence`/`RegimeSegment` 为字段唯一真源做 camelCase 序列化，落实 P2-d）。均为实现细节而非新增能力/依赖/契约。

---

## 9. 评审结论（Review Verdict · Subtask #1）

**结论：有条件通过（Approved with Conditions）。**

方案整体**可行、适度、与既有架构一致**：核心集成面（`summary` 回喂、`ToolRegistry` 注册与异常收敛、`candles_to_df`、`load_range_candles`、`engine/indicators.py` 纯函数、`Candle.confirm`、8 个内置策略 key、`agent/prompts.py` 现有 5 条纪律可平滑接第 6 条）均经**逐行核对真实源码**确认无误；三正交轴分类法 + 「工具算证据 / 提示词管推理」分工 + 两层渐进式提示词的取舍清晰，零新增依赖、不改 DB/WS/REST 契约、文件与函数尺寸有 R9 拆分护栏，符合 `CLAUDE.md` 阈值与「不假数据(C1)/严格因果」硬约束。

评审共发现 **0 个 P0、4 个 P1、5 个 P2**。**全部 4 个 P1 已在本 v2 正文就地修复**（P1-1 payload 数据流误述、P1-2 validate 签名缺 args、P1-3 STRATEGY_REGISTRY 注册副作用/导入来源、P1-4 snapshot 未过滤 forming K 线），P2-a/b/c 亦已顺手收紧。**无 P0/P1 残留**，满足 Subtask #1 的 Definition of Done。

**放行条件（实现期 Subtask #2/#3 须落实，均为 P2 级，不阻塞设计定稿）：**

1. **（P1-3 守护）** `engine/regime.py` 经包导入 `from ..strategies import STRATEGY_REGISTRY`；`test_engine_regime.py` 首行 `import app.strategies` 并断言注册表非空——否则 `candidate_strategies` 用例形同虚设。
2. **（P1-4 守护）** snapshot / timeline-无区间路径取数后必须 `confirm` 过滤剔除 forming bar；补 `test_snapshot_drops_forming_candle`。
3. **（P2-d）** 序列化 `evidence` 时以 §5.1 `RegimeEvidence` 为字段唯一真源，确保 §4.2 payload 与之一致（含 `lastTs`/`lastClose`）。
4. **（P2-e）** 实现完成后补一条 network 冒烟，核对**真实模型**是否照三步脚手架显式产出思维链（离线单测仅能覆盖工具侧证据/脚手架文本，覆盖不到模型遵从度，R5）。
5. 落码时严守 `CLAUDE.md` 阈值（`classify_regime` 按 R9 拆 `_trend/_volatility/_event_features` + `_score_and_resolve`），并按任务环境约定使用跨平台命令（勿 `mkdir -p` / `&&`），提交期精确 `git add <路径>`。

> 满足以上条件后即可进入开发。评审无保留意见的部分占绝大多数，条件均为「实现期注意」而非「方案返工」。
