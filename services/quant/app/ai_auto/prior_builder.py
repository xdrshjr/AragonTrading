"""离线事件研究：两路事件抽取 → 分段统计 → 衰减加权 → 快照装配（§3.3 / §3.4）。

**只在 rebuild 端点被调用**（`run_in_threadpool` 内），决策轮不做任何回放。三条硬约束：

1. **严格因果**：只有 `i + h <= n-1` 的根才产生事件（前瞻窗口走不完的一律剔除，绝不补零），
   入场恒取 `open[i+1]`——与 `SimBroker` 的次根开盘撮合、与 `signal_edge` 的默认口径同源。
2. **恒产毛口径**（`costBasis == "gross"`，`cost_bps` 一律 0）：双重计费会让任何真实 edge 被判死
   （R-1）。加载侧对非 gross 条目 fail-closed 丢弃，两侧共同保证「结构上产不出净额条目」。
3. **策略侧复用实盘信号板本身**（`signals.evaluate_signal_board`）：先验度量的对象必须是「实盘每轮
   真的会喂给模型的那个立场」。任何在此另写一份回放的实现判为不合格（R-3）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..engine.factors.evaluation import MIN_IC_SEGMENTS
from ..engine.regime import per_bar_primaries
from ..engine.signal_edge import SIGNAL_LIBRARY, detect_signal, forward_return_at
from ..engine.walk_forward_purge import plan_segments, purge_crossing_events
from .signal_prior import ALL_REGIME, SignalPriorEntry, entry_to_wire
from .signals import evaluate_signal_board, make_shadow_broker

# 「零亏损样本」专治项：`avgLossBps = 0` 会让 EV 退化为 `p·W`，给出一个不可能的乐观分子（§3.4）。
_MIN_LOSS_SAMPLES = 5

# 段间标准差的「≈0」相对容差（见 `_segment_significance` 的论证）。
_STD_REL_EPS = 1e-9

_SNAPSHOT_VERSION = 1
_ROUND = 6


@dataclass(frozen=True)
class _EventGroup:
    """一路证据的原始事件集（体制分桶之前）。"""

    source: str                  # signal_edge | strategy_replay
    event: str                   # trigger | stance
    direction: str
    indices: list[int]
    run_of: dict[int, int]       # 根索引 → 独立事件段号（相邻根属同一段）
    signal_key: str | None = None
    strategy_id: int | None = None
    strategy_name: str = ""
    fingerprint: str | None = None


@dataclass(frozen=True)
class _BuildCtx:
    """一次快照构建的只读上下文（聚合形参，满足 `CLAUDE.md` 的 ≤5 形参约束）。"""

    opens: np.ndarray
    closes: np.ndarray
    ts: np.ndarray
    regimes: dict[int, str]
    horizon_bars: int
    segments: int
    embargo_bars: int
    half_life_days: float
    min_samples: int
    min_runs: int
    min_segments: int
    built_at_ts: int
    n: int = field(default=0)


def build_prior_snapshot(df: pd.DataFrame, specs: list, frames: list, *, inst_id: str, bar: str,
                         horizon_bars: int, segments: int, embargo_bars: int,
                         half_life_days: float, min_samples: int, min_runs: int,
                         built_at_ts: int, fingerprints: dict[int, str],
                         min_segments: int = MIN_IC_SEGMENTS) -> dict:
    """在 `(instId, bar)` 上产出一份完整先验快照（纯计算，无 IO）。

    Args:
        df: 基础周期全区间帧（`candles_to_df` 产物，升序）。
        specs: 选中策略的运行规格（`build_paper_strategy_spec` 产物；空列表 = 只建 signal_edge 侧）。
        frames: 与 `specs` 一一对齐的策略帧（`build_backtest_frames` 产物，按绝对索引对齐）。
        horizon_bars: 前瞻根数 `h`，必须与 `ai_trade_cost_expected_hold_bars` 同源（§3.2 推论 1）。
        segments: 分段数（默认 6，与 `DEFAULT_IC_SEGMENTS` 同源）。
        embargo_bars: 段末隔离根数。
        half_life_days: 段级时间衰减半衰期（天）。
        min_samples / min_runs / min_segments: `usable` 五判据的三个阈值（§3.4）。
        built_at_ts: 构建时刻（秒）——衰减权重的年龄基准，由调用方注入，本函数不读时钟。
        fingerprints: `{strategyId: 指纹}`（C-6 校验字段的来源，§3.5）。

    Returns:
        §5.2 的快照 dict（可直接 JSON 落 `app_settings`）。
    """
    work = df.reset_index(drop=True)
    ctx = _BuildCtx(
        opens=work["o"].to_numpy(dtype=float), closes=work["c"].to_numpy(dtype=float),
        ts=work["ts"].astype("int64").to_numpy(), regimes=dict(per_bar_primaries(work)),
        horizon_bars=int(horizon_bars), segments=int(segments),
        embargo_bars=int(embargo_bars), half_life_days=float(half_life_days),
        min_samples=int(min_samples), min_runs=int(min_runs), min_segments=int(min_segments),
        built_at_ts=int(built_at_ts), n=len(work))
    groups = _signal_groups(work, ctx) + _strategy_groups(specs, frames, ctx, fingerprints)
    entries = [entry for group in groups for entry in _group_entries(group, ctx)]
    return {"version": _SNAPSHOT_VERSION, "instId": inst_id, "bar": bar,
            "builtAtTs": ctx.built_at_ts, "asOfTs": int(ctx.ts[-1]) if ctx.n else 0,
            "horizonBars": ctx.horizon_bars, "costBasis": "gross", "bars": ctx.n,
            "segments": ctx.segments, "purgeBars": ctx.horizon_bars,
            "embargoBars": ctx.embargo_bars, "halfLifeDays": ctx.half_life_days,
            "minSamples": ctx.min_samples, "minRuns": ctx.min_runs,
            "entries": [entry_to_wire(entry) for entry in entries],
            "coverage": _coverage(entries, specs)}


# ============================ 两路事件抽取（§3.3） ============================
def _signal_groups(df: pd.DataFrame, ctx: _BuildCtx) -> list[_EventGroup]:
    """`signal_edge` 侧：8 个内置具名信号，事件 = **本根触发**（方向由 `SIGNAL_LIBRARY` 固定）。"""
    groups = []
    for key, spec in SIGNAL_LIBRARY.items():
        triggers = detect_signal(df, key).to_numpy(dtype=bool)
        indices = _causal_indices([i for i in range(ctx.n) if triggers[i]], ctx)
        if indices:
            groups.append(_EventGroup(
                source="signal_edge", event="trigger", direction=spec.direction,
                indices=indices, run_of={i: i for i in indices}, signal_key=key))
    return groups


def _strategy_groups(specs: list, frames: list, ctx: _BuildCtx,
                     fingerprints: dict[int, str]) -> list[_EventGroup]:
    """`strategy_replay` 侧：逐根影子回放，事件 = **本根持久立场**（`stance == d`，§3.3）。

    取立场根而非进场边沿，是因为 AI 每轮看到的、也是唯一能拿来做条件的就是板上那个持久 `stance`；
    度量边沿、应用于立场是把两个不同的条件分布强行等同。代价（样本重叠）由 `runN` 判据承担。
    """
    if not specs:
        return []
    shadows = [make_shadow_broker() for _ in specs]
    long_short = {spec.strategy_id: {"long": [], "short": []} for spec in specs}
    for i in range(ctx.n):
        for view in evaluate_signal_board(specs, shadows, frames, i):
            if view.stance in ("long", "short"):
                long_short[view.strategy_id][view.stance].append(i)
    groups = []
    for spec in specs:
        for direction in ("long", "short"):
            indices = _causal_indices(long_short[spec.strategy_id][direction], ctx)
            if indices:
                groups.append(_EventGroup(
                    source="strategy_replay", event="stance", direction=direction,
                    indices=indices, run_of=_runs_of(indices),
                    strategy_id=spec.strategy_id, strategy_name=spec.strategy_name,
                    fingerprint=fingerprints.get(spec.strategy_id)))
    return groups


def _causal_indices(indices: list[int], ctx: _BuildCtx) -> list[int]:
    """严格因果截断（G-3）：前瞻窗口走不完的根一律剔除，绝不补零。"""
    return [i for i in indices if i + ctx.horizon_bars <= ctx.n - 1]


def _runs_of(indices: list[int]) -> dict[int, int]:
    """把根索引映射到独立事件段号——相邻根属同一段（`stance` 断开即新起一段，§3.4 第 5 条）。"""
    out, run_id, prev = {}, 0, None
    for i in indices:
        if prev is not None and i != prev + 1:
            run_id += 1
        out[i] = run_id
        prev = i
    return out


# ============================ 分桶 + 分段统计（§3.4 / §3.5） ============================
def _group_entries(group: _EventGroup, ctx: _BuildCtx) -> list[SignalPriorEntry]:
    """一路证据 → 逐体制桶条目。起算根之前没有体制标签的事件**只进 `all` 桶**（绝不补中性标签）。"""
    buckets: dict[str, list[int]] = {ALL_REGIME: list(group.indices)}
    for i in group.indices:
        label = ctx.regimes.get(i)
        if label:
            buckets.setdefault(label, []).append(i)
    entries = []
    for regime, indices in buckets.items():
        entry = _bucket_entry(group, regime, indices, ctx)
        if entry is not None:
            entries.append(entry)
    return entries


def _bucket_entry(group: _EventGroup, regime: str, indices: list[int],
                  ctx: _BuildCtx) -> SignalPriorEntry | None:
    """单个 `(身份, 体制桶)` 的统计条目；purge 后无样本 ⇒ `None`（不产空条目）。"""
    seg_means, samples = _segment_samples(group, indices, ctx)
    if not samples:
        return None
    rets = [ret for _, ret, _ in samples]
    weights = [w for _, _, w in samples]
    ir, t_stat = _segment_significance(seg_means, ctx.min_segments)
    # `runN` 只数**留下来的**那些根所属的独立事件段：purge 会剔掉跨段事件，用原始 indices 数段
    # 会把已经不在样本里的段也算进独立性，正好放松了本判据要收紧的那件事。
    run_n = (len(samples) if group.source == "signal_edge"
             else len({group.run_of[i] for i, _, _ in samples}))
    loss_n = sum(1 for ret in rets if ret < 0.0)
    stat = {"sample_n": len(samples), "loss_n": loss_n, "segments": len(seg_means),
            "run_n": run_n, "t_stat": t_stat}
    usable, reason = _usability(group.source, stat, ctx)
    return SignalPriorEntry(
        source=group.source, event=group.event, direction=group.direction, regime=regime,
        win_rate=round(_wmean([1.0 if r > 0.0 else 0.0 for r in rets], weights), _ROUND),
        mean_ret_bps=round(_wmean(rets, weights), _ROUND),
        avg_win_bps=round(_side_mean(samples, positive=True), _ROUND),
        avg_loss_bps=round(abs(_side_mean(samples, positive=False)), _ROUND),
        sample_n=len(samples), loss_n=loss_n, segments=len(seg_means), run_n=run_n,
        usable=usable, cost_basis="gross", signal_key=group.signal_key,
        strategy_id=group.strategy_id, strategy_name=group.strategy_name,
        strategy_fingerprint=group.fingerprint,
        ir=None if ir is None else round(ir, _ROUND),
        t_stat=None if t_stat is None else round(t_stat, _ROUND), unusable_reason=reason)


def _segment_samples(group: _EventGroup, indices: list[int],
                     ctx: _BuildCtx) -> tuple[list[float], list[tuple[int, float, float]]]:
    """逐段 purge → 逐段均值 + 事件级 `(根索引, 收益bps, 段级衰减权重)` 样本。"""
    seg_means: list[float] = []
    samples: list[tuple[int, float, float]] = []
    for i0, i1 in plan_segments(ctx.n, ctx.segments):
        kept = purge_crossing_events([i for i in indices if i0 <= i < i1], seg_end=i1,
                                     horizon_bars=ctx.horizon_bars,
                                     embargo_bars=ctx.embargo_bars)
        if not kept:
            continue
        rets = [_ret_bps(i, group.direction, ctx) for i in kept]
        seg_means.append(float(np.mean(rets)))
        weight = _decay_weight(int(ctx.ts[i1 - 1]), ctx)
        samples.extend((i, ret, weight) for i, ret in zip(kept, rets))
    return seg_means, samples


def _ret_bps(i: int, direction: str, ctx: _BuildCtx) -> float:
    """第 `i` 根事件的方向调整**毛**前瞻收益（bps）；估计量唯一真源在 `signal_edge`。"""
    return forward_return_at(ctx.opens, ctx.closes, i, ctx.horizon_bars,
                             entry_at="next_open", direction=direction) * 1e4


def _decay_weight(seg_end_ts: int, ctx: _BuildCtx) -> float:
    """段级时间衰减权重 `0.5 ** (age_days / half_life)`；半衰期 ≤0 ⇒ 关闭衰减（恒 1.0）。"""
    if ctx.half_life_days <= 0:
        return 1.0
    age_days = max(ctx.built_at_ts - seg_end_ts, 0) / 86400.0
    return 0.5 ** (age_days / ctx.half_life_days)


def _segment_significance(seg_means: list[float],
                          min_segments: int) -> tuple[float | None, float | None]:
    """段间一致性：`ir = mean/std(ddof=1)`（**不加衰减**——加权会污染 std）+ 有效段数达标才出 t。

    `std ≈ 0`（各段均值近似常数）⇒ `ir = None` ⇒ 整条不可用：一个假装有信息的常数比缺席更危险。
    判据用**相对容差**而非 `std > 0`：等比价格序列的逐段均值在浮点上只差 1e-13，裸判 `> 0` 会产出
    `ir ≈ 2e13` 这种「显著到荒谬」的读数，夹到 `ir_clip` 后与一条真有 edge 的证据完全无法区分。
    """
    if len(seg_means) < 2:
        return None, None
    arr = np.asarray(seg_means, dtype=float)
    std = float(arr.std(ddof=1))
    mean = float(arr.mean())
    if not math.isfinite(std) or std <= max(abs(mean), 1.0) * _STD_REL_EPS:
        return None, None
    ir = mean / std
    effective = len(seg_means)
    return ir, (ir * math.sqrt(effective) if effective >= min_segments else None)


def _usability(source: str, stat: dict, ctx: _BuildCtx) -> tuple[bool, str | None]:
    """§3.4 的**五条** `usable` 判据，按顺序给出首个不满足项作为 `unusableReason`。"""
    checks = (
        (stat["sample_n"] < ctx.min_samples, f"sample_n<{ctx.min_samples}"),
        (stat["segments"] < ctx.min_segments, f"segments<{ctx.min_segments}"),
        (stat["t_stat"] is None, "t_stat_unavailable"),
        (stat["loss_n"] < _MIN_LOSS_SAMPLES, f"loss_n<{_MIN_LOSS_SAMPLES}"),
        (source == "strategy_replay" and stat["run_n"] < ctx.min_runs,
         f"run_n<{ctx.min_runs}"),
    )
    for failed, reason in checks:
        if failed:
            return False, reason
    return True, None


# ============================ 加权聚合原语与覆盖率 ============================
def _wmean(values: list[float], weights: list[float]) -> float:
    total = sum(weights)
    if total <= 0.0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights)) / total


def _side_mean(samples: list[tuple[int, float, float]], *, positive: bool) -> float:
    """盈利侧 / 亏损侧的衰减加权均值；该侧无样本 ⇒ `0.0`（由 `lossN ≥ 5` 判据兜住乐观分子）。"""
    side = [(ret, w) for _, ret, w in samples if (ret > 0.0 if positive else ret < 0.0)]
    return _wmean([ret for ret, _ in side], [w for _, w in side])


def _coverage(entries: list[SignalPriorEntry], specs: list) -> dict:
    """快照级覆盖率摘要（运维读 `GET /ai-trade/priors` 时不必自己数条目）。"""
    usable = [e for e in entries if e.usable]
    return {"strategies": len(specs),
            "strategiesWithPrior": len({e.strategy_id for e in usable
                                        if e.source == "strategy_replay"}),
            "signals": len(SIGNAL_LIBRARY),
            "signalsWithPrior": len({e.signal_key for e in usable
                                     if e.source == "signal_edge"})}
