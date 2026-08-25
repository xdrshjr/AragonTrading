"""桶级**充分统计量**累加器（W-B）—— 纯函数 / 纯数据、零 IO、完全可复现。

这是「真正的增量物化」的承重结构。它存在的全部理由是一条产品承诺：需求原文说「经验是累积的，
随着交易的进行能够进行不断的更新」，而 `8e05dd9` 的实现是**每一次物化都从事件表全量重建**，
`materialize_max_events`（20 万）越界之后**直接停止自动物化**——那句承诺在库变大之后是会停下来的。

三条使增量成为可能的性质，本模块逐条落地：

1. **绝大多数统计量是可合并的充分统计量**：`n / wins / Σx / Σx² / Σhold / Σcost / Σ|gross| /
   Σedgeerr / min ts / max ts / excluded` 全部满足 `merge(a, b) = a + b`。
2. **唯二不可合并的两个量各有有界解**：
   - `medianNetBps` → **稀疏定宽直方图**（默认 1 bps 一箱，夹到 ±5000，只存非零箱）。确定性、
     可合并、误差 ≤ 一个箱宽，且它今天**只被详情抽屉的一格读**、不进任何 prompt ⇒ 近似是安全的，
     但**必须在 UI 上标注**（J-2 后半 / A-35）。
   - `symbols`（去重标的数，`resolve_scope` 的 `global_min_symbols` 闸要用）→ **有界集合**
     （默认 ≤64）；溢出即记 `symbols_overflow` 并按 `>= cap` 处理（任何 `min_symbols` 阈值都会
     通过）。真实使用中标的数远小于 64。
3. **四个时刻戳必须各存一份**：`compute_stats` 在 `kept` 非空 / 空两种情况下 `firstTs` / `lastTs`
   取自**不同的集合**（合格者 / 全部）。只存两个就无法与全量逐值等价（G-10 会当场红）。

`to_stats()` 的产物必须与 `aggregate.compute_stats` **逐值相等**——这条由 G-10 机械钉死。任何
「顺手改一下舍入」的修改都会让增量与全量分叉，而那是本轮最危险的失败形态（R-1）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .aggregate import (
    _ROUND,
    BucketStats,
    EventView,
    _pick_template,
)
from .aggregate import Bucket as _Bucket

# 直方图的默认分箱与夹紧（可由 `Settings` 覆盖，见 `HistSpec`）。
DEFAULT_BIN_BPS = 1.0
DEFAULT_CLAMP_BPS = 5000.0
DEFAULT_SYMBOLS_CAP = 64


@dataclass(frozen=True)
class HistSpec:
    """直方图与有界集合的旋钮（聚合形参，全部来自 `Settings`）。"""

    bin_bps: float = DEFAULT_BIN_BPS
    clamp_bps: float = DEFAULT_CLAMP_BPS
    symbols_cap: int = DEFAULT_SYMBOLS_CAP

    @classmethod
    def from_settings(cls, settings) -> "HistSpec":
        return cls(
            bin_bps=float(settings.ai_trade_experience_hist_bin_bps),
            clamp_bps=float(settings.ai_trade_experience_hist_clamp_bps),
            symbols_cap=int(settings.ai_trade_experience_symbols_cap))


@dataclass
class BucketAccumulator:
    """一个桶的充分统计量。`fold_event` 单调累加，`merge` 可结合，`to_stats` 还原。

    字段名即落库列名（`ai_experience_buckets`），故这里改名等于改表——**别顺手改**。
    """

    n: int = 0                       # 合格（`v1:I-7`）事件数
    excluded: int = 0                # 被口径闸排除的事件数
    wins: int = 0
    sum_net: float = 0.0
    sum_net_sq: float = 0.0
    sum_hold: float = 0.0
    sum_cost: float = 0.0
    cost_n: int = 0
    sum_abs_gross: float = 0.0
    gross_n: int = 0
    sum_edge_err: float = 0.0
    edge_n: int = 0
    hist: dict[int, int] = field(default_factory=dict)
    symbols: set[str] = field(default_factory=set)
    symbols_overflow: bool = False
    first_ts_kept: int = 0
    last_ts_kept: int = 0
    first_ts_all: int = 0
    last_ts_all: int = 0
    cost_fallback: bool = False

    @property
    def total(self) -> int:
        """桶的事件总数（含被口径闸排除的）—— `applies_in` 边 `support` 的口径（★ E-15）。"""
        return int(self.n) + int(self.excluded)


def fold_event(acc: BucketAccumulator, event: EventView, *, spec: HistSpec,
               cost_fallback: bool) -> None:
    """把一条事件折进累加器（**就地**修改；调用方保证同一条事件只折一次）。

    Args:
        acc: 目标累加器。
        event: 事件视图。
        spec: 直方图与有界集合的旋钮。
        cost_fallback: 该事件的 `costRegime` 是否用了全库分位（`label_cost_regime` 的第二个返回值）。
    """
    exit_ts = int(event.exit_wall_ts or 0)
    acc.first_ts_all = exit_ts if acc.first_ts_all == 0 else min(acc.first_ts_all, exit_ts)
    acc.last_ts_all = max(acc.last_ts_all, exit_ts)
    acc.cost_fallback = bool(acc.cost_fallback or cost_fallback)
    if len(acc.symbols) < max(1, int(spec.symbols_cap)):
        acc.symbols.add(str(event.inst_id))
    elif str(event.inst_id) not in acc.symbols:
        acc.symbols_overflow = True
    if not event.eligible:
        acc.excluded += 1
        return
    net = float(event.net_bps)
    acc.n += 1
    acc.wins += 1 if net > 0 else 0
    acc.sum_net += net
    acc.sum_net_sq += net * net
    acc.sum_hold += float(event.hold_seconds or 0.0)
    if event.cost_bps is not None:
        acc.sum_cost += float(event.cost_bps)
        acc.cost_n += 1
    if event.gross_bps is not None:
        acc.sum_abs_gross += abs(float(event.gross_bps))
        acc.gross_n += 1
    if event.edge_error_bps is not None:
        acc.sum_edge_err += float(event.edge_error_bps)
        acc.edge_n += 1
    index = _bin_index(net, spec)
    acc.hist[index] = acc.hist.get(index, 0) + 1
    acc.first_ts_kept = exit_ts if acc.first_ts_kept == 0 else min(acc.first_ts_kept, exit_ts)
    acc.last_ts_kept = max(acc.last_ts_kept, exit_ts)


def merge(left: BucketAccumulator, right: BucketAccumulator) -> BucketAccumulator:
    """两个累加器合并（可结合、可交换——这正是「增量 ≡ 全量」成立的代数理由）。"""
    out = BucketAccumulator(
        n=left.n + right.n, excluded=left.excluded + right.excluded,
        wins=left.wins + right.wins, sum_net=left.sum_net + right.sum_net,
        sum_net_sq=left.sum_net_sq + right.sum_net_sq,
        sum_hold=left.sum_hold + right.sum_hold,
        sum_cost=left.sum_cost + right.sum_cost, cost_n=left.cost_n + right.cost_n,
        sum_abs_gross=left.sum_abs_gross + right.sum_abs_gross,
        gross_n=left.gross_n + right.gross_n,
        sum_edge_err=left.sum_edge_err + right.sum_edge_err,
        edge_n=left.edge_n + right.edge_n,
        symbols_overflow=bool(left.symbols_overflow or right.symbols_overflow),
        cost_fallback=bool(left.cost_fallback or right.cost_fallback))
    out.hist = dict(left.hist)
    for index, count in right.hist.items():
        out.hist[index] = out.hist.get(index, 0) + count
    out.symbols = set(left.symbols) | set(right.symbols)
    out.first_ts_kept = _min_ts(left.first_ts_kept, right.first_ts_kept)
    out.first_ts_all = _min_ts(left.first_ts_all, right.first_ts_all)
    out.last_ts_kept = max(left.last_ts_kept, right.last_ts_kept)
    out.last_ts_all = max(left.last_ts_all, right.last_ts_all)
    return out


def to_stats(acc: BucketAccumulator, bucket: _Bucket, *, spec: HistSpec) -> BucketStats:
    """累加器 → `BucketStats`；**与 `aggregate.compute_stats` 逐值相等**（G-10 钉死）。

    唯一允许的差异是 `medianNetBps`：直方图给的是「箱心」，与真实中位数相差 ≤ 一个箱宽
    （默认 1 bps）。这个量只被详情抽屉的一格读、不进任何 prompt，且 UI 必须标「≈」（A-35）。

    Args:
        acc: 该桶的累加器。
        bucket: 桶身份（`_pick_template` 要看它的签名）。
        spec: 直方图旋钮（还原箱心用）。

    Returns:
        与全量路径逐值等价的 `BucketStats`。
    """
    stats = BucketStats(n=acc.n, excluded=acc.excluded,
                        symbols=(max(len(acc.symbols), int(spec.symbols_cap))
                                 if acc.symbols_overflow else len(acc.symbols)))
    stats.costRegimeFallback = bool(acc.cost_fallback)
    if acc.n <= 0:
        stats.firstTs = int(acc.first_ts_all)
        stats.lastTs = int(acc.last_ts_all)
        stats.template = _pick_template(bucket, stats)
        return stats
    stats.wins = int(acc.wins)
    stats.winRate = round(acc.wins / acc.n, _ROUND)
    stats.meanNetBps = round(acc.sum_net / acc.n, _ROUND)
    stats.medianNetBps = _hist_median(acc.hist, acc.n, spec)
    stats.stdNetBps = _std_from_sums(acc.n, acc.sum_net, acc.sum_net_sq)
    stats.tStat = _t_from_sums(acc.n, acc.sum_net, acc.sum_net_sq)
    stats.meanHoldS = round(acc.sum_hold / acc.n, 2)
    stats.meanCostBps = (round(acc.sum_cost / acc.cost_n, _ROUND) if acc.cost_n else None)
    mean_gross = (acc.sum_abs_gross / acc.gross_n) if acc.gross_n else None
    if stats.meanCostBps is not None and mean_gross:
        stats.costShare = round(stats.meanCostBps / mean_gross, _ROUND)
    stats.meanEdgeErrorBps = (round(acc.sum_edge_err / acc.edge_n, _ROUND)
                              if acc.edge_n else None)
    stats.firstTs = int(acc.first_ts_kept)
    stats.lastTs = int(acc.last_ts_kept)
    stats.template = _pick_template(bucket, stats)
    return stats


def from_events(events: list[EventView], *, spec: HistSpec,
                fallbacks: list[bool] | None = None) -> BucketAccumulator:
    """一批事件 → 一个累加器（全量路径写累加器底座时用；也是 G-10 的对照实现）。"""
    acc = BucketAccumulator()
    for index, event in enumerate(events):
        fold_event(acc, event, spec=spec,
                   cost_fallback=bool(fallbacks[index]) if fallbacks else False)
    return acc


def row(acc: BucketAccumulator) -> dict:
    """累加器 → 数据层行字典（列名口径的**唯一**转换点）。"""
    return {
        "n": int(acc.n), "excluded": int(acc.excluded), "wins": int(acc.wins),
        "sum_net": float(acc.sum_net), "sum_net_sq": float(acc.sum_net_sq),
        "sum_hold": float(acc.sum_hold), "sum_cost": float(acc.sum_cost),
        "cost_n": int(acc.cost_n), "sum_abs_gross": float(acc.sum_abs_gross),
        "gross_n": int(acc.gross_n), "sum_edge_err": float(acc.sum_edge_err),
        "edge_n": int(acc.edge_n),
        "hist": {str(k): int(v) for k, v in sorted(acc.hist.items())},
        "symbols": sorted(acc.symbols), "symbols_overflow": bool(acc.symbols_overflow),
        "first_ts_kept": int(acc.first_ts_kept), "last_ts_kept": int(acc.last_ts_kept),
        "first_ts_all": int(acc.first_ts_all), "last_ts_all": int(acc.last_ts_all),
        "cost_fallback": bool(acc.cost_fallback),
    }


def from_row(data: dict) -> BucketAccumulator:
    """数据层行字典 → 累加器（`row` 的逆，两者必须逐字对称）。"""
    hist = data.get("hist") or {}
    return BucketAccumulator(
        n=int(data.get("n") or 0), excluded=int(data.get("excluded") or 0),
        wins=int(data.get("wins") or 0), sum_net=float(data.get("sum_net") or 0.0),
        sum_net_sq=float(data.get("sum_net_sq") or 0.0),
        sum_hold=float(data.get("sum_hold") or 0.0),
        sum_cost=float(data.get("sum_cost") or 0.0), cost_n=int(data.get("cost_n") or 0),
        sum_abs_gross=float(data.get("sum_abs_gross") or 0.0),
        gross_n=int(data.get("gross_n") or 0),
        sum_edge_err=float(data.get("sum_edge_err") or 0.0),
        edge_n=int(data.get("edge_n") or 0),
        hist={int(k): int(v) for k, v in hist.items()},
        symbols=set(data.get("symbols") or ()),
        symbols_overflow=bool(data.get("symbols_overflow")),
        first_ts_kept=int(data.get("first_ts_kept") or 0),
        last_ts_kept=int(data.get("last_ts_kept") or 0),
        first_ts_all=int(data.get("first_ts_all") or 0),
        last_ts_all=int(data.get("last_ts_all") or 0),
        cost_fallback=bool(data.get("cost_fallback")))


# ---------------- 内部：直方图与从和式还原的统计量 ----------------
def _bin_index(value: float, spec: HistSpec) -> int:
    """净额 bps → 箱号（夹到 ±clamp；`floor` 保证确定性且与符号无关地单调）。"""
    clamp = abs(float(spec.clamp_bps))
    bounded = max(-clamp, min(clamp, float(value)))
    return int(math.floor(bounded / max(1e-9, float(spec.bin_bps))))


def _hist_median(hist: dict[int, int], count: int, spec: HistSpec) -> float | None:
    """稀疏直方图的中位数（**箱心**近似，误差 ≤ 一个箱宽）；空 ⇒ None。

    偶数样本取两个中位箱心的平均，与 `aggregate._median` 的语义对齐；奇偶两侧都确定性。
    """
    if count <= 0 or not hist:
        return None
    order = sorted(hist.items())
    lower = _nth_bin(order, (count - 1) // 2)
    upper = _nth_bin(order, count // 2)
    center = ((lower + 0.5) + (upper + 0.5)) / 2 * float(spec.bin_bps)
    return round(center, _ROUND)


def _nth_bin(order: list[tuple[int, int]], rank: int) -> int:
    """升序箱序列里第 `rank`（0 基）个样本所在的箱号。"""
    seen = 0
    for index, count in order:
        seen += count
        if rank < seen:
            return index
    return order[-1][0]


def _std_from_sums(count: int, total: float, total_sq: float) -> float | None:
    """由 `(n, Σx, Σx²)` 还原样本标准差；与 `aggregate._std` **逐值同口径**（ddof=1）。"""
    if count < 2:
        return None
    var = _variance(count, total, total_sq)
    return round(math.sqrt(var), _ROUND) if var > 0 else 0.0


def _t_from_sums(count: int, total: float, total_sq: float) -> float | None:
    """由 `(n, Σx, Σx²)` 还原 t 统计量；与 `aggregate._t_stat` **逐值同口径**。"""
    if count < 2:
        return None
    var = _variance(count, total, total_sq)
    if var <= 0:
        return None
    mean = total / count
    return round(mean / (math.sqrt(var) / math.sqrt(count)), _ROUND)


def _variance(count: int, total: float, total_sq: float) -> float:
    """`Σ(x−x̄)² / (n−1)`，由和式展开为 `(Σx² − (Σx)²/n) / (n−1)`。

    ⚠️ 负零裁剪不是洁癖：浮点相消会让理论上为 0 的方差算出 `-1e-13`，而 `math.sqrt` 对它抛
    `ValueError` —— 一条「同一批事件全量绿、增量红」的最难查的分叉。
    """
    numerator = total_sq - (total * total) / count
    return max(0.0, numerator) / (count - 1)


def _min_ts(left: int, right: int) -> int:
    """两个时刻戳取小，`0` 视为「没有」（★ E-12：与批次顺序无关的单调量）。"""
    if not left:
        return int(right)
    if not right:
        return int(left)
    return int(min(left, right))
