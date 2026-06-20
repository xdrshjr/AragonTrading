# 多周期（Multi-Timeframe）策略信号方案设计

> 文档版本：**v2**（方案设计 v1 / Subtask #0 → 评审修复 v2 / Subtask #1）
> Feature slug：`multi-timeframe-strategy-signals`
> 适用仓库：Aragon Trading（`apps/web` + `services/quant` 单仓多包）
> 关联文档：`docs/plans/aragon-trading-platform/spec.md`（基础平台 v2）、`.claude-index/index.md`
> v2 变更摘要：修复 P1-1（`bool(NaN)` 误判趋势门控）、P1-2（`compare` 并集取数重写）、P1-3（paper 按需刷新仅限 live 非预热）、P1-4（`trend_pullback` 周期参数单一真源），见下文「评审记录」。

---

## 评审记录（Review Notes，v2 / Subtask #1）

> 评审人：Anthropic 工程团队资深评审。维度：**可行性 / 完整性 / 一致性 / 规模适配**。
> 已逐节核对真实代码：`okx/types.py`（`BAR_SECONDS`、`Candle.confirm=True`）、`okx/rest_client.py`（`get_candles(before/after)` 的**反向游标**语义已实测，本方案用法正确）、`engine/backtester.py`（`run_backtest`/`candles_to_df` 返回 `ts,o,h,l,c,vol` 升序 RangeIndex）、`strategies/base.py`、`api/strategies.py`（`backtest`/`compare_strategies` 现状）、`paper/session.py`（`_process_confirmed`/`_prime_history`/`_run_replay`）、`strategies/_trend_structure.py`（`attach_htf_regime`/`HTF_COLUMNS`/`_attach_trend_flags`）、`ema_cross_scalper.py`、`trend_pullback_long.py`、`apps/web/lib/types.ts`（`ParamSchema` 已支持 `enum`/`bool`/`options`/`label`，前端自动渲染成立）、`apps/web/components/Strategy/StrategyDetail.tsx`（checkbox/select 已就绪）、`schemas.py`（`BacktestReq.limit ≤ 1000`）。
> **核心结论**：`causal_htf_merge` 以「高周期收盘时间 + `merge_asof(backward)`」对齐，经推演与既有 `_trend_structure` 的 `shift(1)` 桶语义**完全等价且严格无未来函数**（基础桶 B 的行只看到收盘于 `B*htf` 的上一桶 B-1），方案整体**可行**。下列 **P1 已就地修复**，**P2** 列为落地条件（见「评审结论」）。

| 编号 | 维度 | 严重度 | 问题摘要 | 处置 |
|---|---|---|---|---|
| P1-1 | 一致性/正确性 | **P1** | `causal_htf_merge` 对未匹配基础行产生 NaN。若把 `htf_*_uptrend/downtrend` 布尔列在 merge **之前**算好再并入，未匹配行的布尔列会变成 NaN，而 Python `bool(float('nan')) is True`，趋势门控被静默反转（「无数据」被误判为「上行中」），直接违反 G5。 | 已修：§3.2/§3.7 规定布尔趋势列必须在 merge **之后**由数值列派生（NaN 比较得 `False`）；策略门控一律显式 `pd.isna` 判定缺失，绝不依赖 `bool()`；新增 T-bool 用例（§7）。 |
| P1-2 | 完整性 | **P1** | §3.5 `compare` 并集取数片段引用了**未定义**的 `max_warmup`，且与现有「循环内逐 sid 实例化策略」的实现不兼容——取数前拿不到各策略的 `timeframes()`。 | 已修：§3.5 重写为「预实例化候选 → 求并集 + `max_warmup` → 一次取数 → 循环复用实例下发子集」两段式，None/失效行在两段一致跳过；每个 run 落库 `config.htfBars`。 |
| P1-3 | 可行性/正确性 | **P1** | §3.6 的 `_process_confirmed` 为 live / replay / priming **共用**路径；若在其中无条件做「按需 REST 刷新」，高速回放与历史预热会反复打 OKX（限频风暴），且对历史根而言刷新「当前」HTF 毫无意义。 | 已修：§6.4 明确按需刷新**仅限 `source=="live"` 且非 `seeded`**；replay / priming 按其历史区间**一次性预填** HTF 窗口；补 `_htf_bars`/`_htf_windows` 初始化与降级。 |
| P1-4 | 一致性/规模适配 | **P1** | §3.7 给 `trend_pullback` 新增 `htf_bar` 枚举，与其**既有** `htf_seconds` 参数并存会 desync：真实 HTF 路径与重采样兜底路径可能用**不同周期**。 | 已修：`trend_pullback` **不**新增枚举；`timeframes()` 由现有 `htf_seconds` 经 `seconds_to_bar` 派生（标准 bar→真实 HTF；非标准如 7200s→`None`→重采样兜底）。单一真源、完全向后兼容、参数面更小。`ema_cross_scalper` 无既有 HTF 旋钮，保留新增 `htf_bar`。 |
| P2-1 | 完整性 | P2 | HTF 取数 `limit=min(1000,…)` 的截断风险。 | 经核对 `BacktestReq.limit ≤ 1000` 且取数含 `htf_warmup_bars` 缓冲，可证 `min(1000)×htf_seconds ≥ 1000×base_seconds`，**当前不会截断**；§3.5 注明该不变量，放宽基础 `limit` 时须复核。 |
| P2-2 | 完整性 | P2 | `htfBars` 仅在 backtest 链路落库。 | §3.5/§6.3 补充 `compare`/`paper` 各 run 同样写 `config.htfBars`。 |
| P2-3 | 一致性/发布 | P2 | `trend_pullback` 迁移到真实 HTF 后结果与 v1（重采样）不再逐字节一致，历史 run 不可复现（属**预期**行为变化）。 | §7/§9 要求：策略 `description` 显式说明、并保留「离线重采样兜底路径对 v1 的回归基线」作为对照（兜底路径仍与 v1 等价）。 |
| P2-4 | 健壮性 | P2 | `causal_htf_merge` 依赖 `base_df` 为唯一 RangeIndex（今由 `candles_to_df` 保证）。 | §3.2 增加不变量断言提示，防未来某 `prepare` 重索引后错位。 |
| P2-5 | 规模适配（可选） | P2 | paper 实时 HTF（per-HTF `deque` + 边界刷新）是本期最大风险面。 | 建议以策略参数/灰度开关先行；不阻塞回测/对比两条主链路上线。 |

---

## 1. Overview（概述）

当前平台的回测与实时模拟均**只在用户当前选择的单一 K 线周期（`bar`）上驱动策略**。用户在工具栏选 5m，点击「运行回测」，后端 `POST /strategies/{id}/backtest` 只拉取 5m 历史 K 线，`run_backtest(strategy, candles, bar, …)` 构造**单一周期** DataFrame，`strategy.prepare(df, bar)` 只算这一个周期的指标，`strategy.on_candle(df, i, ctx)` 也只能看到这一个周期。除两个「顺势回踩」策略外，其余策略（`ema_cross_scalper`、`momentum_breakout`、`bollinger_reversion`、`vwap_rsi_momentum`、`long_shadow_reversal`、`range_scalping_strategy`）都**只依据单周期指标判别买卖**。这与「超短交易要先看大周期趋势再在小周期波段内操作」的真实交易逻辑相悖，正是用户反馈的核心问题。

值得说明的是，仓库里已有一套**因果重采样**的高周期（HTF, Higher-Timeframe）机制：`strategies/_trend_structure.py::attach_htf_regime` 会把基础周期 K 线按 `ts // htf_seconds` 分桶聚合，在桶级序列上算 EMA/ATR/摆动点后 `shift(1)` 桶再因果映射回基础行。它**严格无未来函数**，是很好的基础，但有两点不足：(1) 只有 `trend_pullback_long/short` 在用；(2) HTF 数据是**从基础窗口重采样**得到的，受基础窗口长度限制——例如 5m + 1H，需要约 600 根 5m 才能凑出约 50 根 1H 桶，HTF EMA-50 才勉强激活，历史一旦不足高周期指标长期为 NaN、策略「只是不出信号而非报错」。

本方案在**不破坏既有单周期策略**、**不引入假数据**（C1 硬约束）的前提下，落地一套**一等公民的多周期框架**：让策略**声明**它需要参考的高周期（如 5m 执行 + 1H 趋势过滤），数据层**真实拉取**这些高周期的 OKX K 线、**因果对齐**到基础时间线（每根基础 K 线只能看到「已收盘的上一根高周期 K 线」），再把高周期指标列并入基础 DataFrame 供策略综合判别。框架同时覆盖**回测 / 多策略对比 / 实时模拟**三条链路（三者共用 `prepare`/`on_candle`），并将既有两个顺势回踩策略平滑迁移到「真实高周期 K 线」上（保留重采样兜底）。最终效果：策略可同时综合多周期指标决定买卖，5m 剥头皮可被 1H 趋势门控，从根上修复「只看单一周期」的缺陷。

## 2. Goals / Non-Goals（目标与非目标）

**Goals**
- G1：为 `BaseStrategy` 增加**声明式多周期**能力——策略声明所需高周期 bar 列表，框架负责取数与对齐。
- G2：提供**严格因果**（无未来函数，平台硬规则 R4）的高周期→基础周期对齐工具，使用**真实高周期 K 线**而非仅靠重采样。
- G3：回测 / 对比 / 实时模拟三链路统一接入；**单周期既有策略行为零变化**（黄金回归测试保证）。
- G4：至少让一个高频策略（`ema_cross_scalper`）获得「大周期趋势过滤」能力，直接对标用户的「5m 看 1H」诉求；并把 `trend_pullback_long/short` 迁移到真实高周期。
- G5：OKX 不可达 / 高周期历史不足时，**显式错误或安全降级**（NaN 守卫 → 不出信号），绝不静默假数据。

**Non-Goals**
- N1：本期**不做**前端「手动多选多周期」的复杂 UI；高周期由策略参数驱动，复用既有 `paramsSchema` 自动渲染（仅新增可选信息性展示）。
- N2：不改 SimBroker 撮合内核、不改 metrics 口径、不改 DB schema（仅复用既有 `candle_cache` 与 `run.config`）。
- N3：不接入真实下单（Phase 1 模拟硬约束不变）。
- N4：不为高周期单独引入 WebSocket 多路复用；实时模式高周期走「按需 REST 刷新」（见 §6.4）。

## 3. Technical Design（技术设计）

### 3.1 架构总览

```text
策略声明 timeframes() ──► API 取数层（真实 OKX 高周期 K 线）
                              │  base candles + {htf_bar: htf_candles}
                              ▼
        run_backtest / PaperSession  ── df = strategy.prepare(df, bar)         (基础周期指标, 既有)
                                       df = strategy.attach_timeframes(df, bar, frames)  (新增: 因果并入HTF列)
                              │
                              ▼
        逐根 on_candle(df, i, ctx) 综合「基础周期 + 高周期」列产出 Signal
```

核心思路：**保留** `prepare(df, bar)` 与 `on_candle(df, i, ctx)` 的既有签名与调用点，**新增**一个可选 hook `attach_timeframes(df, bar, frames)`，把「真实高周期 K 线」因果并入基础 `df` 的带前缀列（如 `htf_1H_ema_fast`）。策略只需在 `on_candle` 里读这些列做门控/确认。默认实现为恒等函数，故**未声明高周期的策略完全不走新代码路径**。

### 3.2 因果对齐（无未来函数）——新增 `engine/mtf.py`

一根「开盘时间 = T」的高周期 K 线覆盖区间 `[T, T+htf_seconds)`，**在 `T+htf_seconds` 时刻才收盘**。因此基础周期在时刻 `t` 只能引用「收盘时间 ≤ t」的高周期 K 线，即满足 `T + htf_seconds <= t`。用 `pandas.merge_asof(direction="backward")` 按「高周期收盘时间」对齐即可，天然杜绝引用未收盘的当根高周期（与现有 `_trend_structure` 的 `shift(1)` 桶语义一致，但数据源换成真实高周期 K 线、历史不再受基础窗口限制）。

```python
# services/quant/app/engine/mtf.py  （新增，纯计算、无 IO）
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable
import pandas as pd
from ..okx.types import BAR_SECONDS

# 秒数 → 标准 OKX bar（仅这些可向 OKX 取真实 K 线）
_SECONDS_TO_BAR = {v: k for k, v in BAR_SECONDS.items()}

def bar_to_seconds(bar: str) -> int:
    """标准 bar → 秒；未知 bar 抛 ValueError（边界一次性校验）。"""
    if bar not in BAR_SECONDS:
        raise ValueError(f"未知 bar: {bar!r}")
    return BAR_SECONDS[bar]

def seconds_to_bar(seconds: int) -> str | None:
    """秒 → 标准 OKX bar；非标准秒数返回 None（调用方据此回退重采样）。"""
    return _SECONDS_TO_BAR.get(int(seconds))

@dataclass
class MTFBundle:
    """一次驱动所需的多周期数据包：基础 df + 各高周期真实 K 线 df。"""
    base_df: pd.DataFrame
    bar: str
    base_seconds: int
    frames: dict[str, pd.DataFrame]   # htf_bar -> 升序 ts 的 OHLC df

def causal_htf_merge(
    base_df: pd.DataFrame,
    htf_df: pd.DataFrame,
    htf_seconds: int,
    *,
    compute: Callable[[pd.DataFrame], pd.DataFrame],
    prefix: str,
) -> pd.DataFrame:
    """把高周期指标因果并入基础行，返回与 base_df 同索引、带 prefix 的列。

    compute: 在「桶级/高周期序列」上算指标，须自身因果（EMA/ATR/确认型摆动点）。
    对齐键 = 高周期收盘时间 (ts + htf_seconds)，merge_asof backward：
    基础行 t 只取「收盘时间 <= t」的最近一根高周期 K 线，杜绝未来根。
    """
    feats = compute(htf_df).reset_index(drop=True)
    close_ts = (htf_df["ts"].astype("int64") + int(htf_seconds)).reset_index(drop=True)
    right = pd.concat([close_ts.rename("_close_ts"), feats], axis=1).sort_values("_close_ts")
    left = base_df[["ts"]].reset_index().sort_values("ts")   # 保留原索引于 'index'
    merged = pd.merge_asof(left, right, left_on="ts", right_on="_close_ts",
                           direction="backward")
    merged = merged.set_index("index").reindex(base_df.index)  # 还原基础行顺序
    out = merged.drop(columns=["ts", "_close_ts"])
    return out.add_prefix(prefix)
```

要点：
- `compute` 只接收高周期 OHLC（列 `ts,o,h,l,c,vol`），复用 `engine/indicators.py` 与 `_trend_structure.py` 的纯函数，保证自身因果。
- `merge_asof` 要求两侧按键升序——`get_candles` 已返回升序，且我们显式 `sort_values`，稳健。
- 历史不足处 `merge_asof` 自然给 NaN，由策略 `on_candle` 的既有 NaN 守卫兜住（不报错、不出信号）。
- **【P1-1 修复｜布尔趋势列必须在 merge 之后派生】**：`compute` **只产出数值列**（`ema_fast/ema_slow/slope/atr/swing_*`），**不得**在 `compute` 内产出 `uptrend/downtrend` 等布尔列。原因：`merge_asof` 对未匹配基础行回填 NaN，而 Python `bool(float('nan')) is True`——若布尔列先于 merge 计算并入，未匹配行会得到「被误判为 True」的趋势位，静默反转门控（违反 G5）。正确做法是**先 merge 数值列、再由数值列派生布尔位**：此时 `nan > nan` → `False`，缺失行自然落到「非上行、非下行」。本仓库既有 `_attach_trend_flags` 正是「就地由数值列派生、与 NaN 比较得 False」，故规定其在 `causal_htf_merge` 返回**之后**对已并入的数值列调用一次（见 §3.7）。
- **【P2-4 修复｜索引不变量】**：`causal_htf_merge` 依赖 `base_df` 为**唯一 RangeIndex**（今由 `candles_to_df` 的 `reset_index(drop=True)` 保证）。实现时在函数入口断言 `base_df.index.is_unique and base_df.index.is_monotonic_increasing`，避免未来某 `prepare` 重索引导致 `set_index("index").reindex` 错位；不满足则显式抛错（边界一次性校验）。

### 3.3 `BaseStrategy` 扩展（向后兼容）——`strategies/base.py`

```python
class BaseStrategy(ABC):
    ...
    def timeframes(self) -> list[str]:
        """本策略需参考的【高于基础周期】的标准 OKX bar 列表（由 params 派生）。
        默认 [] —— 不取额外周期，行为与现状完全一致。"""
        return []

    def htf_warmup_bars(self) -> int:
        """高周期指标暖机所需的最小【高周期】根数（用于 API 计算取数窗口）。"""
        return 60

    def attach_timeframes(self, df: pd.DataFrame, bar: str,
                          frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """把真实高周期 K 线因果并入基础 df。默认恒等（未声明周期者零开销）。
        frames: {htf_bar: 升序 OHLC df}；缺某周期时由子类决定降级（重采样兜底）。"""
        return df
```

- `timeframes()` 与 `htf_warmup_bars()` 均为 **params 纯函数**（不触运行态），符合既有「`warmup()` 早于 `prepare()`」生命周期约束。
- `attach_timeframes` 在 `prepare` **之后**调用，子类内部用 `mtf.causal_htf_merge` 生成带前缀列。

### 3.4 回测编排改造——`engine/backtester.py`

`run_backtest` 增加可选入参 `htf_frames`，在 `prepare` 后做一次 `attach_timeframes`，逐根循环**完全不变**：

```python
def run_backtest(strategy, candles, bar, *, initial_capital, taker_fee_bps,
                 slippage_bps, leverage=1.0,
                 htf_frames: dict[str, list] | None = None) -> BacktestResult:
    df = candles_to_df(candles)
    n = len(df)
    if n == 0: ...                      # 既有空护
    df = strategy.prepare(df, bar)
    if htf_frames:                      # 仅声明了周期才进入
        frames = {b: candles_to_df(cs) for b, cs in htf_frames.items()}
        df = strategy.attach_timeframes(df, bar, frames)
    ...                                 # SimBroker 四阶段循环原样不动
```

`htf_frames=None`（默认）→ 与今日逐字节等价（G3 保证）。

### 3.5 API 取数层改造——`api/strategies.py`

新增私有助手，按策略声明拉取真实高周期 K 线：

```python
async def _load_htf_frames(body, base_seconds: int, timeframes: list[str],
                           htf_warmup_bars: int) -> dict[str, list[Candle]]:
    """对每个高周期 bar 拉真实 OKX K 线，覆盖回测区间 + 暖机，过滤未确认与越界。"""
    frames: dict[str, list[Candle]] = {}
    for tf in timeframes:
        htf_seconds = bar_to_seconds(tf)
        if htf_seconds <= base_seconds:        # 必须严格高于基础周期
            continue
        span_bars = (body.endTs - body.startTs) // htf_seconds + 1
        # 【P2-1 不变量】limit 上限 1000：因 BacktestReq.limit ≤ 1000，基础区间最多 1000×base_seconds；
        # 而 HTF 取 min(1000, span+warmup) 根、含 warmup 缓冲，可证 min(1000)×htf_seconds ≥ 1000×base_seconds，
        # 故 HTF 时间跨度恒 ≥ 基础区间，不会因 1000 截断而留下早段 NaN。若未来放宽基础 limit，须同步复核此处。
        limit = min(1000, int(span_bars) + htf_warmup_bars)
        candles = await get_rest_client().get_candles(
            body.instId, tf, limit, before=body.endTs + 1)     # before=「早于」语义→左滚回溯
        frames[tf] = [c for c in candles
                      if c.ts <= body.endTs and getattr(c, "confirm", True)]
    return frames
```

`backtest()` 端点接入（取真实数据失败→既有 OKXError→5xx 显式错误，C1）：

```python
strategy = cls(params)
base_seconds = bar_to_seconds(body.bar)
candles = await _load_backtest_candles(body)              # 基础周期（既有）
await repo.upsert_candles(session, body.instId, body.bar, candles)
tfs = [tf for tf in strategy.timeframes() if tf != body.bar]
htf_frames = await _load_htf_frames(body, base_seconds, tfs,
                                    strategy.htf_warmup_bars()) if tfs else {}
for tf, cs in htf_frames.items():
    await repo.upsert_candles(session, body.instId, tf, cs)   # 入缓存复用
run = await repo.create_run(..., config={..., "htfBars": list(htf_frames.keys())})
result = await run_in_threadpool(run_backtest, strategy, candles, body.bar,
            initial_capital=..., taker_fee_bps=..., slippage_bps=..., leverage=...,
            htf_frames=htf_frames)
```

`compare_strategies()` 接入（**P1-2 修复**）：候选策略可能声明**不同**高周期，取其**并集**只取一次（避免 N× OKX 调用触发限频，R1）。注意现状 `compare_strategies` 是在 `for sid in ordered_ids` 循环**内**才实例化策略；要在取数前拿到各策略的 `timeframes()`，必须改为**两段式**——先预实例化全部有效候选并缓存，再求并集取数，最后循环复用同一批实例（不重复实例化、None/失效行在两段一致跳过）：

```python
# —— 第一段：预实例化有效候选（保序、去重 id 后），缓存 (sid, row, strategy) ——
prepared: list[tuple[int, object, BaseStrategy]] = []
for sid in ordered_ids:
    row = await repo.get_strategy_row(session, sid)
    if row is None:
        continue
    cls = get_strategy_class(row.template_key)
    if cls is None:
        continue
    try:
        params = {**cls.meta.default_params, **json.loads(row.params_json or "{}")}
    except json.JSONDecodeError:
        params = dict(cls.meta.default_params)
    prepared.append((sid, row, cls(params)))

# —— 第二段：并集取数（只取一次） ——
base_seconds = bar_to_seconds(body.bar)
union_tfs = sorted({tf for _, _, s in prepared for tf in s.timeframes()
                    if bar_to_seconds(tf) > base_seconds})
max_warmup = max((s.htf_warmup_bars() for _, _, s in prepared), default=60)
union_frames = await _load_htf_frames(body, base_seconds, union_tfs, max_warmup) \
    if union_tfs else {}
for tf, cs in union_frames.items():
    await repo.upsert_candles(session, body.instId, tf, cs)   # 入缓存复用

# —— 第三段：循环复用实例，逐策略只下发它声明的子集，并各自落库 ——
for sid, row, strategy in prepared:
    htf_frames = {tf: union_frames[tf] for tf in strategy.timeframes()
                  if tf in union_frames}
    run = await repo.create_run(..., config={..., "htfBars": list(htf_frames.keys())})  # P2-2
    result = await run_in_threadpool(run_backtest, strategy, candles, body.bar,
                ..., htf_frames=htf_frames)
    ...  # persist + items.append 原样
```

要点：并集只触发一次 `_load_htf_frames`（T8 断言 REST 调用计数）；`max_warmup` 取候选的最大暖机根数；每个 run 的 `config.htfBars` 落库其**实际**下发子集（P2-2，透明追溯）。

### 3.6 实时模拟接入——`paper/session.py`（见 §6.4 详述）

`PaperSession.__init__` 末尾派生并初始化高周期状态（仅当策略声明了高周期时才有开销）：

```python
self._htf_bars: list[str] = [tf for tf in strategy.timeframes()
                             if bar_to_seconds(tf) > BAR_SECONDS.get(bar, 60)]
self._htf_windows: dict[str, deque] = {b: deque(maxlen=_WINDOW_MAX) for b in self._htf_bars}
self._htf_last_bucket: dict[str, int] = {}   # 各高周期上次刷新所在桶，用于「跨边界才刷新」
```

`_process_confirmed` 在 `strategy.prepare(...)` 后、`on_candle` 前追加（**P1-3 修复：按需刷新仅限 live 非预热**）：

```python
if self._htf_bars:
    # 仅实时段(source=="live" 且非 seeded)才按边界做按需 REST 刷新；
    # replay / priming 的历史区间已在启动时一次性预填，绝不逐根刷新（见 §6.4）。
    if self.source == "live" and not seeded:
        await self._maybe_refresh_htf(int(candle.ts))
    frames = {b: candles_to_df(list(win)) for b, win in self._htf_windows.items() if win}
    df = self.strategy.attach_timeframes(df, self.bar, frames)
```

`_maybe_refresh_htf(base_ts)` 内对每个 `self._htf_bars`：仅当 `base_ts // htf_seconds` 相对 `self._htf_last_bucket[bar]` **变化**（跨过高周期边界）才 `await get_rest_client().get_candles(...)` 取最近窗口、按 ts 去重并入 `deque`，并更新 `_htf_last_bucket`；未跨边界直接复用窗口（限频 R1/R3）。取数异常吞为「窗口不更新」（不阻塞撮合，降级见 §6.4-5）。

### 3.7 既有顺势回踩策略迁移 + 高频策略升级

- `_trend_structure.py`：新增 `regime_from_htf_frame(htf_df, …)`——直接在**真实高周期 OHLC** 上算 `HTF_COLUMNS` 中的**数值列**（`htf_ema_fast/ema_slow/slope/atr/high/low/swing_low/swing_high`，复用现有桶级指标逻辑，**省去**「从基础重采样 + shift 桶」步骤，因果性改由 `causal_htf_merge` 的收盘时间对齐保证）。**【P1-1】`regime_from_htf_frame` 不产出布尔趋势列**；`htf_uptrend/htf_downtrend` 由既有 `_attach_trend_flags` 在 `causal_htf_merge` **合并之后**对已并入数值列调用一次派生（未匹配行 `nan>nan→False`，绝不出现 `bool(nan)→True`）。保留现有 `attach_htf_regime`（重采样）作为**兜底**。
- `trend_pullback_long.py` / `trend_pullback_short.py`（**P1-4 修复：周期参数单一真源，不新增枚举**）：
  - **不**新增 `htf_bar` 枚举（避免与既有 `htf_seconds` desync）。`timeframes()` 由**现有** `htf_seconds` 派生：`tf = seconds_to_bar(int(params["htf_seconds"]))`；若 `tf` 是标准 OKX bar 且其秒数 > 基础周期则返回 `[tf]`，否则（如旧参 `7200s` 无对应标准 bar）返回 `[]` → 走重采样兜底（R-5）。
  - 列前缀用 `htf_`（**非** `htf_<bar>_`），使 `causal_htf_merge` 产出的列名与既有 `HTF_COLUMNS` **完全一致**，`on_candle` 读取的列名零改动。
  - `prepare` 不再内部重采样 HTF；改在 `attach_timeframes` 里：若 `frames` 含该高周期 → `regime_from_htf_frame` + `causal_htf_merge`（prefix=`htf_`）产出数值列、再 `_attach_trend_flags` 派生布尔列；**缺失则回退**现有 `attach_htf_regime`（用 `bar_to_seconds(tf)` 作为 `htf_seconds`，保证真实/兜底两路周期一致；离线/取数失败仍可跑）。
- `ema_cross_scalper.py`（对标用户「5m 看 1H」，无既有 HTF 旋钮，新增枚举合理）：新增 `htf_bar`（`enum`，默认 `1H`）、`htf_trend_filter`（`bool`，默认 `true`）、`htf_required`（`bool`，默认 `false`）。`timeframes()` 在开启过滤且 `bar_to_seconds(htf_bar) > 基础周期` 时返回 `[htf_bar]`。`attach_timeframes` 用高周期 EMA 快/慢算 `htf_<bar>_ema_fast/slow` 数值列，**合并后**派生 `htf_<bar>_uptrend/downtrend` 布尔列。
  - **【P1-1】`on_candle` 门控必须显式判 NaN，不得依赖 `bool()`**：取 `up = row[f"htf_{htf_bar}_uptrend"]`，`if pd.isna(up): allow = not htf_required`（缺失时：`htf_required=false`→放行，`true`→禁开）；否则金叉仅当 `up is True` 放行、死叉仅当 `down is True` 放行。杜绝 `bool(nan)→True` 把「无数据」误判为「同向」。

### 3.8 前端（最小改动）

- 高周期由策略参数驱动，新增的 `htf_bar` / `htf_trend_filter` 等会**自动**经既有 `StrategyDetail` 的 `paramsSchema` 渲染（`enum`/`bool` 已支持），**回测/对比/paper 请求体形状不变**。
- 可选信息性增强：在 `StrategyDetail` 顶部描述下，依据 `strategy.params` 中的 `htf_bar` 渲染一枚「参考周期：1H」徽标，帮助用户理解「本策略综合多周期」。不改任何请求路径。

## 4. File / Module Change Plan（文件 / 模块变更计划）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `services/quant/app/engine/mtf.py` | 新建 | MTF 核心：`MTFBundle`、`bar_to_seconds`/`seconds_to_bar`、`causal_htf_merge`（无未来函数对齐）。 |
| `services/quant/app/strategies/base.py` | 修改 | `BaseStrategy` 增 `timeframes()`/`htf_warmup_bars()`/`attach_timeframes()` 默认 hook（向后兼容）。 |
| `services/quant/app/engine/backtester.py` | 修改 | `run_backtest` 增可选 `htf_frames`，`prepare` 后调用 `attach_timeframes`；循环不变。 |
| `services/quant/app/api/strategies.py` | 修改 | 新增 `_load_htf_frames`；`backtest`/`compare` 按策略声明拉真实高周期并下发、入缓存、写 run.config。 |
| `services/quant/app/paper/session.py` | 修改 | `PaperSession` 维护高周期滚动窗口（预填 + 按需限频刷新），`_process_confirmed` 接入 `attach_timeframes`。 |
| `services/quant/app/strategies/_trend_structure.py` | 修改 | 新增 `regime_from_htf_frame`（真实高周期 OHLC 直算 `HTF_COLUMNS` **数值列**，布尔列由 `_attach_trend_flags` 在 merge 后派生，P1-1）；保留重采样兜底。 |
| `services/quant/app/strategies/trend_pullback_long.py` | 修改 | `timeframes()` 由**现有** `htf_seconds` 经 `seconds_to_bar` 派生（**不新增** `htf_bar`，P1-4）；HTF 改走真实高周期（prefix=`htf_`，列名不变）+ 缺失回退重采样（兜底用 `bar_to_seconds(tf)` 同步周期）。 |
| `services/quant/app/strategies/trend_pullback_short.py` | 修改 | 同上（做空镜像）。 |
| `services/quant/app/strategies/ema_cross_scalper.py` | 修改 | 加 `htf_bar`/`htf_trend_filter`/`htf_required`；高周期趋势门控金叉/死叉，门控**显式判 NaN**（P1-1），对标「5m 看 1H」。 |
| `apps/web/components/Strategy/StrategyDetail.tsx` | 修改（可选） | 依据 `params.htf_bar` 渲染「参考周期」信息徽标；请求路径不变。 |
| `services/quant/tests/test_mtf.py` | 新建 | `causal_htf_merge` 无未来函数 + `seconds_to_bar` 映射/回退单测。 |
| `services/quant/tests/test_strategies.py` | 修改 | 新增 `ema_cross_scalper` 高周期门控用例 + 单周期黄金回归（行为零变化）。 |
| `services/quant/tests/test_trend_pullback.py` | 修改 | 新增「真实高周期 vs 重采样兜底」一致性/激活用例。 |
| `services/quant/tests/test_api.py` | 修改 | 回测接入高周期取数（高周期入缓存、run.config 含 `htfBars`）的集成用例。 |

## 5. Interface Design（接口设计）

### 5.1 后端 Python 内部接口（新增/变更签名）

```python
# engine/mtf.py
def bar_to_seconds(bar: str) -> int
def seconds_to_bar(seconds: int) -> str | None
def causal_htf_merge(base_df, htf_df, htf_seconds, *, compute, prefix) -> pd.DataFrame

# strategies/base.py（BaseStrategy 新增，均有默认实现）
def timeframes(self) -> list[str]
def htf_warmup_bars(self) -> int
def attach_timeframes(self, df, bar, frames: dict[str, pd.DataFrame]) -> pd.DataFrame

# engine/backtester.py（新增关键字参数，默认 None → 行为不变）
def run_backtest(..., htf_frames: dict[str, list[Candle]] | None = None) -> BacktestResult

# api/strategies.py（新增私有助手）
async def _load_htf_frames(body, base_seconds, timeframes, htf_warmup_bars) -> dict[str, list[Candle]]
```

### 5.2 HTTP / 请求体

- `POST /strategies/{id}/backtest`、`POST /strategies/compare`、`POST /paper/start`、`POST /paper/batch/start`：**请求体形状不变**。高周期完全由策略 `params` 服务端派生（`ema_cross_scalper` 用新增 `htf_bar`；`trend_pullback` 用既有 `htf_seconds` 经 `seconds_to_bar` 派生，P1-4），无需前端传参；前端 `api.ts` 与请求类型零改动。
- `run.config`（落库的 JSON）**新增** `htfBars: string[]` 字段用于透明追溯（无 DB schema 变更，`config_json` 本就是自由 JSON）。`runs` 响应 `run_out` 不强制暴露该字段（保持兼容），如需可后续追加。

### 5.3 策略参数 schema（前端自动渲染，示例：`ema_cross_scalper`）

```jsonc
"htf_bar":          {"type": "enum", "default": "1H",
                     "options": ["15m","30m","1H","4H","1D"], "label": "高周期(趋势过滤)"},
"htf_trend_filter": {"type": "bool", "default": true,  "label": "启用高周期趋势过滤"},
"htf_required":     {"type": "bool", "default": false, "label": "高周期数据缺失时禁止开仓"}
```

## 6. Data Model（数据模型）

### 6.1 复用既有 `candle_cache`（无 schema 变更）
高周期 K 线经 `repo.upsert_candles(session, instId, htf_bar, candles)` 落入既有 `candle_cache`（主键 `(instId, bar, ts)`）。`bar` 列天然区分周期，无需新表/新列。

### 6.2 内存形态
- `MTFBundle.frames: dict[str, pd.DataFrame]`：键为高周期 bar，值为列 `ts,o,h,l,c,vol` 的升序 DataFrame。
- `causal_htf_merge` 输出：与基础 `df` 同索引、列名带前缀。前缀由调用方决定：**新策略 / 多高周期**用 `htf_<bar>_<feature>`（如 `htf_1H_ema_fast`、`htf_1H_uptrend`）以区分多个周期；**`trend_pullback` 迁移**用 `htf_`（单高周期，与既有 `HTF_COLUMNS` 列名一致，`on_candle` 零改动，见 §3.7）。两种情形下布尔列均在 merge 后派生（P1-1）。
- `PaperSession._htf_windows: dict[str, deque[dict]]`（`maxlen=_WINDOW_MAX=600`）：每个高周期的滚动 K 线窗口。

### 6.3 `run.config_json` 新增字段
```jsonc
{ ...既有字段..., "htfBars": ["1H"] }   // 本次回测实际参考的高周期列表
```

## 6.4 实时模拟（paper/live）高周期处理细则

实时模式 `_process_confirmed` 每根**已确认**基础 K 线驱动一次撮合。**关键修复（P1-3）**：`_process_confirmed` 是 live / replay / priming **三种来源共用**的路径，因此「按需 REST 刷新」必须**仅在 `source=="live"` 且 `seeded==False`** 时发生；否则高速回放与历史预热会把「当前」HTF 反复打 OKX（限频风暴）且对历史根毫无意义。高周期窗口策略：

1. **启动预填（覆盖各自历史区间）**：
   - **live**：`start()`（`_prime_history` / `_load_warmup_window` 之前或并行）对每个 `self._htf_bars`，REST 拉 `htf_warmup_bars + N` 根真实历史填入 `_htf_windows[bar]`；并据预填末根初始化 `_htf_last_bucket`。预填须覆盖 priming 的近端历史区间（`priming_bars` 对应时长），确保**早段 seeded 基础根**也能对齐到历史上正确的高周期根（失败不阻塞，等首个 live 根触发刷新）。
   - **replay**：在 `_run_replay` 起始按 `replay_bars` 对应时长**一次性预填** HTF 历史窗口即可（回放是固定历史区间，HTF 一次取全）。
2. **按需限频刷新（仅 live 非 seeded）**：在 `_process_confirmed` 内，经 `source=="live" and not seeded` 守卫后调用 `_maybe_refresh_htf`，仅当「当前基础 K 线 ts 跨过该高周期边界」（`ts // htf_seconds` 相对 `_htf_last_bucket` 变化）才 REST 拉一次最近高周期窗口并按 ts 去重并入 `deque`。未跨边界复用窗口，避免每根都打 OKX（限频 R1/R3）。
3. **因果保证**：`attach_timeframes` 仍用 `causal_htf_merge` 的收盘时间对齐，基础行只看到已收盘高周期 K 线；即使窗口里混入「当前未收盘高周期根」，对齐键 `ts+htf_seconds > 基础ts` 也会被 backward 合并自动排除——**故即便 live 窗口含最新高周期根，对早段 seeded 基础根也不会泄漏未来数据**。
4. **回放（replay）模式**：见第 1 条，预填一次后逐根回放**不刷新**（由 `source=="live"` 守卫天然跳过）。
5. **降级**：高周期取数失败 → 对应 `_htf_windows[bar]` 为空 → §3.6 中以 `if win` 跳过该空帧 → `attach_timeframes` 走子类「缺失回退」（重采样兜底或不出信号），**绝不假数据**。

## 7. Testing & Acceptance Criteria（测试与验收）

**单元测试（`test_mtf.py`）**
- T1（无未来函数·核心）：构造基础 5m + 高周期 1H，断言**基础行在 1H 收盘前只能看到上一根已收盘 1H**；把某根 1H 的值改大，验证它**只**影响其收盘时间之后的基础行，不回溯污染更早行。
- T2：`seconds_to_bar` 对标准秒数正确映射、对非标准秒数返回 `None`；`bar_to_seconds` 未知 bar 抛 `ValueError`。
- T3：高周期历史不足时输出列为 NaN（不抛异常）。
- T-bool（**P1-1 回归**）：构造未匹配的早段基础行，断言 `causal_htf_merge` 输出的数值列为 NaN 后，经 `_attach_trend_flags` 派生的 `htf_*_uptrend/downtrend` 为 **`False` 而非 NaN/True**；并断言策略门控对 NaN 行按 `htf_required` 决策（`false`→放行 / `true`→禁开），**绝不**因 `bool(nan)` 误判为同向。

**策略测试（`test_strategies.py` / `test_trend_pullback.py`）**
- T4（黄金回归·G3）：所有 `timeframes()==[]` 的既有策略，回测成交/净值/指标与改造前**逐字节一致**（固定输入快照对比）。
- T5（高周期门控·G4）：构造「基础 5m 金叉但 1H 处于下行」场景，断言 `ema_cross_scalper` 开启 `htf_trend_filter` 时**不开多**；关闭过滤时开多——证明多周期确实改变决策。
- T6：`trend_pullback_long` 用真实 1H 帧与重采样兜底两条路径，高周期趋势一致时入场判定方向一致；真实帧在历史更充足时 HTF EMA 更早激活。
- T6b（**P2-3 兜底基线**）：`timeframes()==[]`（如旧参 `htf_seconds=7200`）或离线无 `frames` 时，`trend_pullback` 走重采样兜底路径，其成交/净值与改造前 v1 **逐字节一致**（迁移只改「有真实高周期帧」时的行为，兜底路径不回退既有正确性）。

**集成测试（`test_api.py`，离线、注入假 REST 客户端，不依赖网络）**
- T7：声明高周期的策略回测后，`candle_cache` 含高周期 bar 行、`run.config_json.htfBars` 含对应 bar。
- T8：`compare` 多策略不同高周期时，高周期取数为**并集且只取一次**（断言 REST 调用计数）。

**验收标准**
- A1：选 5m + `ema_cross_scalper`（默认开启 1H 过滤）回测，逆 1H 趋势的金叉被过滤，成交数较关闭过滤时显著减少且方向更顺势。
- A2：`pnpm test`（后端 pytest 不含 network + 前端 vitest/tsc）全绿；`scripts/smoke_e2e.py` 真实数据链路（含高周期取数）退出码 0。
- A3：未声明高周期的策略，回测结果与改造前一致（T4 通过）。
- A4：OKX 不可达时仍按 C1 显式 5xx，绝不以假高周期数据兜底。

## 8. Risks & Mitigations（风险与缓解）

| 风险 | 影响 | 缓解 |
|---|---|---|
| R-1 未来函数泄漏（误用未收盘高周期根） | 回测虚高、违反 R4 | 统一走 `causal_htf_merge` 收盘时间 backward 对齐 + 高周期只收 `confirm=True`；T1 专测把关。 |
| R-2 额外 OKX 取数触发限频 | 回测/对比变慢或 429 | 高周期每周期**每次回测仅取一次**；compare 取并集；复用既有 `_RateLimiter` 与缓存 `upsert_candles`；paper 按边界限频刷新。 |
| R-3 高周期历史不足致长期 NaN | 策略「只是不出信号」被误认为坏了 | `htf_warmup_bars` 足量取数 + 真实高周期 K 线（比重采样历史更长）；策略描述明示「数据不足仅无信号非报错」；可选 `htf_required` 让用户自选严格度。 |
| R-4 既有策略回归 | 破坏线上行为 | 新路径仅在 `timeframes()!=[]` 触发；默认 hook 恒等；T4 黄金回归逐字节对比。 |
| R-5 非标准 `htf_seconds`（如 trend_pullback 旧参 7200s）无对应 OKX bar | 取不到真实高周期 | `seconds_to_bar` 返回 `None` → 自动回退现有重采样路径，平滑兼容旧参数。 |
| R-6 实时模式高周期窗口与基础窗口不同步 | 对齐错位 | 仅在跨高周期边界刷新 + `causal_htf_merge` 以收盘时间对齐，错位根被 backward 合并排除；去重 by ts。 |
| R-7 前端误以为需手动多选周期 | 体验困惑 | 高周期由策略参数驱动、`paramsSchema` 自动渲染；加「参考周期」徽标说明，不改请求路径。 |
| R-8 时区/秒级一致性 | 对齐偏移一个周期 | 全系统 ts 已统一秒级 UTC（C2）；对齐只用整数秒运算，`ts // htf_seconds` 分桶与既有 `_trend_structure` 同口径。 |
| R-9 `bool(NaN)→True` 误判（P1-1） | 趋势门控被静默反转，未来/错误信号 | 布尔趋势列一律在 `causal_htf_merge` **之后**由数值列派生（`nan>nan→False`）；策略门控显式 `pd.isna` 判定缺失；T-bool 把关。 |
| R-10 paper 共用路径误刷新（P1-3） | replay/priming 限频风暴、无意义取数 | 按需刷新经 `source=="live" and not seeded` 守卫；replay/priming 一次性预填历史窗口；取数失败降级为不更新窗口。 |
| R-11 `trend_pullback` 双周期旋钮 desync（P1-4） | 真实/兜底两路用不同周期 | 不新增 `htf_bar`；`timeframes()` 与兜底 `htf_seconds` 同源于现有 `htf_seconds`（`seconds_to_bar`/`bar_to_seconds` 互换），单一真源。 |

## 9. Implementation Notes（实施提示，供下游逐行落地）

- 命名遵循仓库约定：高周期派生列统一前缀 `htf_<bar>_`；布尔列用 `is_/has_/uptrend/downtrend` 语义。
- `mtf.py` 必须**纯计算、无 IO**（与 `backtester.py` 同档），所有网络取数留在 `api/strategies.py` 与 `paper/session.py` 边界（符合「外部 IO 限定在 OKX 适配/路由/会话边界」约定）。
- 单文件 ≤ 800 行、单函数 ≤ 50 行、圈复杂度 ≤ 10：`attach_timeframes` 内若分支偏多，抽 `_merge_one(frame, ...)` 辅助函数。
- 错误显式传播：高周期 bar 非法在 `bar_to_seconds` 处一次性抛错；取数失败沿用 `OKXError`→5xx，不吞异常。
- 不改 SimBroker / metrics / DB schema；`run.config` 仅追加键，向后兼容。
- 新增/迁移策略须更新其 `meta.description`，注明「综合 X 周期趋势 + 基础周期 Y 入场」，与既有描述风格一致。
- **【P2-3 发布提示】**：`trend_pullback` 迁移到真实高周期后，**有真实帧**时结果与 v1（重采样）不再逐字节一致——属预期改进（真实高周期历史更长、HTF 指标更早激活），但历史 run 不可复现。须在 `description` 显式说明「高周期改用真实 K 线」，并以 T6b 守住「兜底路径仍等价 v1」的对照基线。
- **【P1-1 落地红线】**：任何读取 `htf_*` 派生列做条件判断的策略代码，**禁止**直接 `bool(col)`/`if col:` 于可能为 NaN 的列上；一律先 `pd.isna(...)` 显式分支，缺失语义由 `htf_required` 等参数决定。

---

> **DoD 自检**：本文件位于 `docs/plans/multi-timeframe-strategy-signals/spec.md`，含 Overview / 技术设计 / 文件变更计划 / 接口设计 / 数据模型 / 测试与验收 / 风险与缓解 全部章节，篇幅远超 800 字，可供下游工程师逐行实现而无需再做架构决策。本节点仅产出设计文档，未改任何源码、未提交。

---

## 评审结论（Review Verdict，v2 / Subtask #1）

**结论：有条件通过（Approved with Conditions）。**

方案在**可行性**上经真实代码核对成立：依赖的 `BAR_SECONDS`、`Candle.confirm`、`get_candles(before/after)` 反向游标、`run_backtest`/`candles_to_df`、前端 `ParamSchema`（`enum`/`bool`）、`schemas.py`（`limit ≤ 1000`）均与方案假设一致；核心 `causal_htf_merge` 的「收盘时间 + backward 对齐」经推演与既有 `_trend_structure` 的 `shift(1)` 桶语义**等价且严格无未来函数**。**完整性 / 一致性** 上发现的 4 个 P1（`bool(NaN)` 门控反转、`compare` 并集取数与现有循环结构不兼容、paper 共用路径误刷新、`trend_pullback` 双周期旋钮 desync）**已在 §3.2 / §3.5 / §3.6 / §6.4 / §3.7 就地修复**，并补 T-bool / T6b 用例与 R-9~R-11 风险项。**规模适配**整体得当；唯 paper 实时 HTF 为最大风险面（P2-5），建议灰度。

放行的前提条件（下游开发 Subtask #2 须遵守）：

1. **（P1-1，红线）** 布尔趋势列必须在 `causal_htf_merge` **之后**派生；策略门控对 `htf_*` 列一律先 `pd.isna` 判定，禁止 `bool(nan)`。须有 T-bool 绿灯。
2. **（P1-2）** `compare_strategies` 按 §3.5 改为「预实例化 → 并集取数（一次）→ 循环复用」三段式；T8 断言 REST 调用计数为并集大小。
3. **（P1-3）** paper 按需刷新仅 `source=="live" and not seeded`；replay/priming 一次性预填历史窗口；高周期取数失败安全降级。
4. **（P1-4）** `trend_pullback` 不新增 `htf_bar`，`timeframes()` 与兜底周期同源于现有 `htf_seconds`；真实/兜底两路周期一致。
5. **（G3 / G5 硬约束）** `timeframes()==[]` 的既有策略须通过 T4 逐字节黄金回归；OKX 不可达按 C1 显式 5xx，绝不假数据。

P2-1~P2-5 为**可后续跟进**项（不阻塞开发）：P2-1 已证当前不截断（注明不变量）；P2-2 `htfBars` 落库已补 `compare`/`paper`；P2-3 行为变化须在策略 `description` 披露并以 T6b 守住兜底基线；P2-4 入口加索引不变量断言；P2-5 paper HTF 建议以参数/灰度开关先行。

> 评审范围：仅修改本 `spec.md`，未触碰任何源码，未执行 `git commit`（符合任务约束）。无 P0 / P1 遗留。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation，Subtask #2）

> 下游实现工程师在按 §4 文件变更表逐项落地时发现的两处方案不自洽与一处补充，均已就地按更正确的等价方案实现，行为与方案意图一致、并通过全部新增/既有用例（后端 103 passed，前端 112 passed，tsc 0 error）。

### D-1（一致性·必须更正）：`regime_from_htf_frame` 须产「无前缀」特征列，否则与 `causal_htf_merge(prefix=...)` 叠加会**双重前缀**

- **现象**：§3.7 描述 `regime_from_htf_frame` 直接产出「`htf_ema_fast/…`（含 `htf_` 前缀）」，而 §3.2 的 `causal_htf_merge` 末尾又 `out.add_prefix(prefix)`、且 §3.7 规定 `trend_pullback` 调 `causal_htf_merge(prefix="htf_")`。两者字面叠加会得到 `htf_htf_ema_fast`，与既有 `HTF_COLUMNS` 列名不符，`on_candle` 读列即 `KeyError`。
- **更正（已实现）**：`regime_from_htf_frame` 返回**无前缀**特征列（`ema_fast/ema_slow/slope/atr/high/low/swing_low/swing_high`）；由 `causal_htf_merge` 统一加 `prefix="htf_"` → 恰好得到 `HTF_COLUMNS` 数值列名。`ema_cross_scalper` 同理：compute 返回无前缀，`prefix="htf_<bar>_"`。该更正使前缀来源**单一**（只在 `causal_htf_merge` 加一次），与方案「列名与既有 `HTF_COLUMNS` 一致、`on_candle` 零改动」的**意图完全一致**。

### D-2（可行性·必须更正）：`trend_pullback.prepare` **保留**重采样兜底（而非「不再重采样」），由 `attach_timeframes` 在有真实帧时覆盖

- **现象**：§3.7 要求「`prepare` 不再内部重采样 HTF；缺失则回退 `attach_htf_regime`」。但 §3.4 的 `run_backtest` **仅在 `htf_frames` 真值时**才调 `attach_timeframes`。当策略声明了高周期、却因「基础周期 == 该 bar」或「非标准 `htf_seconds`（如 7200s → `seconds_to_bar` 为 None）」被上游过滤掉、`htf_frames` 为空时，`attach_timeframes` **不会被调用** → 若 `prepare` 又已剥离重采样，则 HTF 列彻底缺失 → `on_candle` 读 `htf_swing_low` 等列 `KeyError`。既有「仅 `prepare` 直驱」的调用方/用例（如 `test_trend_pullback._count_side`）同样会被打断。
- **更正（已实现）**：`prepare` **保持原样**（继续产出重采样 HTF 兜底列）；`attach_timeframes` 在**有真实帧**时用真实高周期 K 线**覆盖** HTF 列、无帧时为 no-op。等价达成方案目标「有真实帧用真实、无帧回退重采样」，且额外保证：①兜底路径与 v1 **逐字节一致**（`prepare` 未改，T6b 天然成立）；②任何只调 `prepare` 的旧调用方不被破坏。代价仅为「有真实帧时多算一次被覆盖的重采样」，可忽略。

### D-3（补充·与 §6.3/P2-2 一致）：`paper` run.config 的 `htfBars` 落库点

- §4 文件表的 paper 行仅列 `paper/session.py`，但 paper 的 `run.config` 实际在 `paper/manager.py::start` 创建。为落实 §6.3/P2-2「`paper` 各 run 同样写 `config.htfBars`」，在 `manager.start` 计算并写入 `htfBars`（与 `session._htf_bars` 同口径：`timeframes()` 中严格高于基础周期者）。属方案 prose 已述范围，仅落点补全。

> 以上更正不改变方案的任何对外契约（请求体形状不变、`run.config` 仅追加键、`HTF_COLUMNS` 列名不变），亦不触碰 SimBroker / metrics / DB schema；G3 黄金回归与 P1-1/P1-2/P1-3/P1-4 放行条件全部满足。
