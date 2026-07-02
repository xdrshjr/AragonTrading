"""因子评估（evaluation.py）—— signal_edge 事件研究范式的连续值推广 (spec §2.3)。

把「布尔事件 → 前瞻收益」换成「连续因子值 → IC / RankIC / 分位单调性」：纯计算、严格因果、
右端截断绝不补零（沿用 ``signal_edge._horizon_returns`` 截断逻辑）。逐 horizon 输出 IC/RankIC、
按时间分段的 RankIC 序列 IR 与 t 值（K≥6 才出 t）、分位前瞻收益单调性与 top-bottom 价差、
覆盖率与换手代理。空样本一律走安全口径（绝不对空集取均值），度量因子的稳健性与可靠性。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from ..signal_edge import MIN_SIGNALS
from .library import compute_factor

DEFAULT_HORIZONS: tuple[int, ...] = (1, 3, 6, 12)
DEFAULT_QUANTILES = 5
DEFAULT_IC_SEGMENTS = 6
MIN_IC_SEGMENTS = 6   # 有效段数 < 6 不输出 t（样本太短无法稳定切段，评审 C-P2-7）


@dataclass
class QuantileBucket:
    """单个分位档的前瞻收益画像（方向调整后）。"""

    q: int
    meanRet: float
    count: int


@dataclass
class FactorHorizonStat:
    """单 horizon 的因子统计画像（camelCase 与前端对齐，序列化即 asdict）。"""

    h: int
    ic: float | None
    rankIc: float | None
    icMean: float | None
    icStd: float | None
    ir: float | None
    tStat: float | None
    monotonicity: float | None
    topBottomSpread: float | None
    quantiles: list[QuantileBucket] = field(default_factory=list)


@dataclass
class FactorEvalResult:
    """因子评估结果（snapshot，无持久化；字段即 payload 真源 §5.2）。"""

    factorKey: str
    direction: str
    bars: int
    windowStartTs: int
    windowEndTs: int
    coverage: float
    occurrences: int
    horizons: list[FactorHorizonStat] = field(default_factory=list)
    instId: str | None = None
    bar: str | None = None
    note: str = ""


# ============================ 相关系数原语（空样本安全口径） ============================
def _pearson(a: np.ndarray, b: np.ndarray) -> float | None:
    """Pearson 相关；样本 < 2 或任一方差为 0 / 非有限 → None（绝不对退化样本臆造相关）。"""
    if len(a) < 2:
        return None
    sa, sb = float(np.std(a)), float(np.std(b))
    if not (np.isfinite(sa) and np.isfinite(sb)) or sa == 0.0 or sb == 0.0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    """Spearman 秩相关（rank 后取 Pearson，纯 numpy/pandas）。"""
    if len(a) < 2:
        return None
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    return _pearson(ra, rb)


def _dir_mult(direction: str) -> float:
    """方向乘子（short 取 -1，其余 +1）。auto 已在调用前解析为 long/short。"""
    return -1.0 if direction == "short" else 1.0


# ============================ 前瞻收益（严格因果右端截断） ============================
def forward_return(close: np.ndarray, h: int) -> np.ndarray:
    """逐根 h 步前瞻收益 ``c[i+h]/c[i]-1``；尾部 ``i+h > n-1`` 置 NaN（绝不补零 C1）。"""
    n = len(close)
    out = np.full(n, np.nan)
    if 0 < h < n:
        base = close[: n - h]
        future = close[h:]
        safe = base != 0.0
        out[: n - h] = np.where(safe, future / np.where(safe, base, 1.0) - 1.0, np.nan)
    return out


def _paired(values: np.ndarray, fwd: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """取 (因子, 前瞻收益) 均非 NaN 的配对样本（暖机 NaN / 右端截断统一剔除，绝不补零）。"""
    mask = np.isfinite(values) & np.isfinite(fwd)
    return values[mask], fwd[mask]


# ============================ 分段 RankIC 序列 → IR / t ============================
def _segment_ir(f: np.ndarray, r: np.ndarray, segments: int,
                dir_mult: float) -> tuple[float | None, float | None, float | None, float | None]:
    """按时间等切 K 段算逐段 RankIC，返回 (icMean, icStd, ir, tStat)。

    有效段数 < ``MIN_IC_SEGMENTS`` 时 ``tStat=None``（样本太短无法稳定切段，评审 C-P2-7）。
    """
    m = len(f)
    seg_ics: list[float] = []
    for j in range(segments):
        i0, i1 = j * m // segments, (j + 1) * m // segments
        if i1 - i0 >= 2:
            ic = _spearman(f[i0:i1], r[i0:i1])
            if ic is not None:
                seg_ics.append(dir_mult * ic)
    if len(seg_ics) < 2:
        return None, None, None, None
    arr = np.asarray(seg_ics, dtype=float)
    mean = float(arr.mean())
    std = float(arr.std(ddof=1))
    ir = mean / std if std > 0.0 else None
    effective = len(seg_ics)
    t_stat = (ir * np.sqrt(effective)) if (ir is not None and effective >= MIN_IC_SEGMENTS) else None
    return round(mean, 6), round(std, 6), _round(ir), _round(t_stat)


# ============================ 分位前瞻收益单调性 ============================
def _quantile_stats(f: np.ndarray, r: np.ndarray, quantiles: int,
                    dir_mult: float) -> tuple[list[QuantileBucket], float | None, float | None]:
    """分位档均前瞻收益（方向调整）+ 单调性（Spearman(档位, 均收益)）+ top-bottom 价差。"""
    try:
        codes = pd.qcut(f, quantiles, labels=False, duplicates="drop")
    except ValueError:
        return [], None, None
    codes = np.asarray(codes, dtype="float64")
    buckets: list[QuantileBucket] = []
    means: list[float] = []
    for q in range(quantiles):
        sel = codes == q
        if not sel.any():
            continue
        mean_ret = dir_mult * float(r[sel].mean())
        buckets.append(QuantileBucket(q=q, meanRet=round(mean_ret, 6), count=int(sel.sum())))
        means.append(mean_ret)
    if len(buckets) < 2:
        return buckets, None, None
    idx = np.asarray([b.q for b in buckets], dtype=float)
    mono = _spearman(idx, np.asarray(means, dtype=float))
    spread = round(means[-1] - means[0], 6)
    return buckets, _round(mono), spread


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)


def _eval_horizon(values: np.ndarray, close: np.ndarray, h: int, *, dir_mult: float,
                  quantiles: int, segments: int) -> FactorHorizonStat:
    """单 horizon 评估：IC / RankIC / 分段 IR / 分位单调性（配对样本不足走安全口径）。"""
    fwd = forward_return(close, h)
    f, r = _paired(values, fwd)
    if len(f) < 2:
        return FactorHorizonStat(h=h, ic=None, rankIc=None, icMean=None, icStd=None, ir=None,
                                 tStat=None, monotonicity=None, topBottomSpread=None, quantiles=[])
    ic = _pearson(f, r)
    rank_ic = _spearman(f, r)
    ic_mean, ic_std, ir, t_stat = _segment_ir(f, r, segments, dir_mult)
    buckets, mono, spread = _quantile_stats(f, r, quantiles, dir_mult)
    return FactorHorizonStat(
        h=h, ic=_round(None if ic is None else dir_mult * ic),
        rankIc=_round(None if rank_ic is None else dir_mult * rank_ic),
        icMean=ic_mean, icStd=ic_std, ir=ir, tStat=t_stat,
        monotonicity=mono, topBottomSpread=spread, quantiles=buckets)


# ============================ 主入口 ============================
def resolve_direction(values: np.ndarray, close: np.ndarray, direction: str,
                      *, primary_horizon: int) -> str:
    """把 ``auto`` 在评估窗口一次性定号并冻结（评审 C-P1-1）：取主 horizon RankIC 的符号。

    仅用于「评估 / 我的因子」展示。一旦加入组合，``members`` 里的 direction 必须是冻结的
    具体值；``compose_score`` 绝不接受 ``auto``（杜绝打分期前视泄漏）。
    """
    if direction in ("long", "short"):
        return direction
    fwd = forward_return(close, primary_horizon)
    f, r = _paired(values, fwd)
    rank_ic = _spearman(f, r) if len(f) >= 2 else None
    return "short" if (rank_ic is not None and rank_ic < 0) else "long"


def evaluate_factor(df: pd.DataFrame, values: pd.Series, direction: str, *,
                    factor_key: str = "", horizons: tuple[int, ...] = DEFAULT_HORIZONS,
                    quantiles: int = DEFAULT_QUANTILES,
                    ic_segments: int = DEFAULT_IC_SEGMENTS) -> FactorEvalResult:
    """评估单因子对未来收益的解释力（IC / RankIC / IR / 分位单调性，严格因果）。

    Args:
        df: ``candles_to_df`` 产物（列 ts/o/h/l/c/vol，升序）。
        values: 与 df 等长的因子值序列（暖机 NaN 由配对统一剔除）。
        direction: ``long`` | ``short`` | ``auto``（auto 在主 horizon 一次性定号并冻结）。
        factor_key / horizons / quantiles / ic_segments: 元信息与评估参数。

    Returns:
        ``FactorEvalResult``（覆盖率 / 配对样本数 / 逐 horizon 统计 + 谨慎标注）。
    """
    work = df.reset_index(drop=True)
    close = work["c"].to_numpy(dtype=float)
    arr = np.asarray(values, dtype=float)
    sorted_h = sorted({int(h) for h in horizons if int(h) > 0}) or list(DEFAULT_HORIZONS)
    resolved = resolve_direction(arr, close, direction, primary_horizon=sorted_h[0])
    dir_mult = _dir_mult(resolved)
    stats = [_eval_horizon(arr, close, h, dir_mult=dir_mult, quantiles=quantiles,
                           segments=max(ic_segments, MIN_IC_SEGMENTS)) for h in sorted_h]
    coverage = float(np.isfinite(arr).mean()) if len(arr) else 0.0
    _, primary_r = _paired(arr, forward_return(close, sorted_h[0]))
    occurrences = int(len(primary_r))
    note = "samples_sparse" if occurrences < MIN_SIGNALS else ""
    return FactorEvalResult(
        factorKey=factor_key, direction=resolved, bars=len(work),
        windowStartTs=int(work["ts"].iloc[0]) if len(work) else 0,
        windowEndTs=int(work["ts"].iloc[-1]) if len(work) else 0,
        coverage=round(coverage, 4), occurrences=occurrences, horizons=stats, note=note)


def evaluate_factor_key(df: pd.DataFrame, key: str, params: dict | None, direction: str, *,
                        bar: str = "", horizons: tuple[int, ...] = DEFAULT_HORIZONS,
                        quantiles: int = DEFAULT_QUANTILES,
                        ic_segments: int = DEFAULT_IC_SEGMENTS) -> FactorEvalResult:
    """便捷封装：先 ``compute_factor`` 再 ``evaluate_factor``（key 写入结果元信息）。"""
    values = compute_factor(df, key, params, bar=bar)
    result = evaluate_factor(df, values, direction, factor_key=key, horizons=horizons,
                             quantiles=quantiles, ic_segments=ic_segments)
    result.bar = bar or None
    return result


def eval_result_to_dict(result: FactorEvalResult) -> dict:
    """序列化为 camelCase dict（字段即 dataclass 字段，§5.2 唯一真源）。"""
    return asdict(result)


# ============================ 标的组聚合（组内均值 IC，factor-lab-symbol-groups §3.3） ============================
_EVAL_METRIC_KEYS = ("ic", "rankIc", "icMean", "icStd", "ir", "tStat", "monotonicity", "topBottomSpread")


def _weighted_mean(pairs: list[tuple]) -> float | None:
    """``(value, weight)`` 列表 → 加权均值；跳过 None / 非有限值 / 非正权重；无有效对 → None（绝不臆造 0）。"""
    num = 0.0
    den = 0.0
    for value, weight in pairs:
        if value is None:
            continue
        fv = float(value)
        fw = float(weight)
        if not (np.isfinite(fv) and fw > 0.0):
            continue
        num += fv * fw
        den += fw
    return num / den if den > 0.0 else None


def _find_horizon(horizons: list[dict], h: int | None) -> dict | None:
    return next((stat for stat in horizons if stat.get("h") == h), None)


def _aggregate_quantiles(stats: list[tuple[dict, int]]) -> list[dict]:
    """逐分位 index ``q``：``meanRet`` 按 count 加权、``count`` 求和（各标的分位档可不全，按 q 对齐）。"""
    by_q: dict[int, list[tuple]] = {}
    for stat, _occ in stats:
        for bucket in stat.get("quantiles") or []:
            by_q.setdefault(bucket.get("q"), []).append((bucket.get("meanRet"), bucket.get("count") or 0))
    out: list[dict] = []
    for q in sorted(by_q.keys()):
        pairs = by_q[q]
        mean_ret = _weighted_mean(pairs)
        if mean_ret is None:
            continue
        out.append({"q": q, "meanRet": _round(mean_ret), "count": sum(int(c or 0) for _v, c in pairs)})
    return out


def _aggregate_horizons(results: list[dict]) -> list[dict]:
    """逐 horizon 对齐（以焦点标的顺序为基准，各标的同 horizons 集）→ 逐指标 occurrences 加权、分位 count 加权。"""
    focus_horizons = results[0].get("horizons") or []
    out: list[dict] = []
    for base in focus_horizons:
        h = base.get("h")
        stats = [(stat, int(r.get("occurrences") or 0))
                 for r in results if (stat := _find_horizon(r.get("horizons") or [], h)) is not None]
        agg: dict = {"h": h}
        for key in _EVAL_METRIC_KEYS:
            agg[key] = _round(_weighted_mean([(s.get(key), w) for s, w in stats]))
        agg["quantiles"] = _aggregate_quantiles(stats)
        out.append(agg)
    return out


def _primary_rank_ic(result: dict) -> float | None:
    """主 horizon（最小，评估已升序）的 RankIC，供 perSymbol 逐标的贡献展示。"""
    horizons = result.get("horizons") or []
    return horizons[0].get("rankIc") if horizons else None


def aggregate_eval_results(results: list[dict], symbols: list[str], direction: str) -> dict:
    """把逐标的（均已同向）评估结果聚合为「组内均值」结果 dict（factor-lab-symbol-groups §3.3）。

    各标的结果须已在**同一冻结方向**下评估（``auto`` 由焦点标的解析一次后冻结下发，见 ``service.evaluate``），
    否则符号相反的逐标的指标混入同一均值不自洽。逐指标只对**非空**值按 ``occurrences`` 加权平均，全空→null
    （绝不臆造 0）；分位桶逐档 count 加权；``perSymbol`` 回填逐标的贡献（绝不静默丢弃入聚合的标的）。

    Args:
        results: 逐标的 ``eval_result_to_dict`` 产物（顺序即 ``symbols`` 顺序，[0] 为焦点标的）。
        symbols: 参与聚合的有效标的（去空去重后）。
        direction: 冻结后的方向（``long`` | ``short``），顶层回显。

    Returns:
        与 ``FactorEvalResult`` 同形的 dict + 附加 ``symbols`` / ``perSymbol`` / ``aggregated``；
        ``instId`` 置焦点标的。

    Raises:
        ValueError: ``results`` 为空。
    """
    if not results:
        raise ValueError("aggregate_eval_results 需至少一个标的评估结果")
    focus = results[0]
    occ_total = sum(int(r.get("occurrences") or 0) for r in results)
    bars_total = sum(int(r.get("bars") or 0) for r in results)
    coverage = _weighted_mean([(r.get("coverage"), r.get("bars") or 0) for r in results])
    if any(r.get("note") == "bar_incompatible" for r in results):
        note = "bar_incompatible"
    elif occ_total < MIN_SIGNALS:
        note = "samples_sparse"
    else:
        note = ""
    per_symbol = [{
        "instId": r.get("instId"),
        "occurrences": int(r.get("occurrences") or 0),
        "primaryRankIc": _primary_rank_ic(r),
        "coverage": r.get("coverage"),
    } for r in results]
    return {
        "factorKey": focus.get("factorKey", ""),
        "direction": direction,
        "bars": bars_total,
        "windowStartTs": focus.get("windowStartTs", 0),
        "windowEndTs": focus.get("windowEndTs", 0),
        "coverage": round(coverage, 4) if coverage is not None else 0.0,
        "occurrences": occ_total,
        "horizons": _aggregate_horizons(results),
        "instId": symbols[0] if symbols else focus.get("instId"),
        "bar": focus.get("bar"),
        "note": note,
        "symbols": list(symbols),
        "perSymbol": per_symbol,
        "aggregated": len(results) > 1,
    }
