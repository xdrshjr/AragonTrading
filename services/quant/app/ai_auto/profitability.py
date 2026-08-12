"""跨会话盈利体检纯核心（ai-trade-realized-edge-calibration 轴 B）。

今天全仓所有盈利读数的作用域最大到**一个分组**，而一个分组的往返数中位只有个位数（28 组里 10 组
零成交）。于是每一层新纪律上线时都带着 5 笔样本的「证据」，下一轮再叠一层，证据基座永远追不上机制
复杂度。本模块把逐笔往返在**全部历史**上池化，产出一张会明确写「样本不足，不下结论」的体检卡。

⚠️ **本模块的 `winRate` 未做样本外验证，不得作为 `kelly_fraction` 的 `p_win` 输入**（R-10）。
它是一个**历史统计**，不是对未来的估计；把它接进仓位求解等于把过拟合直接换算成敞口。

八条硬约束：

1. **一切比例都池化**：`winRate = Σwins / Σtrips`，**永远不对比值求平均**（D-2 实测：池化 40.74%
   vs 各组胜率的算术平均 36.11%，差 4.63 pp）。切片的每一行同样池化且必带 `trips` 分母。
   **每个比例 / 中位数类读数都必须与它自己的分母同行出现**——`expectancyR` 的分母是 `rSamples`，
   与 `expectancyUsd` 的 `roundTrips` **不是同一个数**。
2. **`breakevenPayoff = (1 − winRate) / winRate`**，`winRate ∈ {0, 1}` ⇒ None（不造 ∞）。
   它与 `payoff` 必须**成对**出现——单独一个 1.014 没有任何意义。
3. **成本类读数只在带费子样本上算**：`medianCostBps` 的分母是 `feeTrips` 而**不是** `roundTrips`
   ——实测 193 笔里 172 笔（89.1%）的腿费恒 0 且那是「没记」不是「零」（D-9），全样本中位会算出
   `0.000`。`travelToCostRatio` 在 `feeCoverage < _MIN_FEE_COVERAGE` 时一律 None。
4. **`tStat` 复用 `attribution._t_stat`**——绝不另写一份（两份 t 统计量迟早分叉）。
5. **结论表冻结**（`_VERDICTS`，判据自上而下取首个命中）；`tStat is None` 的两条前置分支不是冗余：
   约束 4 自己写明 `n<2` 或零方差 ⇒ None，而后四条全是与阈值做大小比较，Python 里 `None <= -2.0`
   直接抛 `TypeError`、端点当场 500。
6. **口径推定必须随读数一起上屏**：`costBasisAssumedTrips` 是**计数**而不是布尔——布尔表达不了
   「89% 的样本靠推定」这件事。
7. **`verdict.detail` 由后端生成并复述分母**（前端不拼数字）。
8. **中位数一律走「空即 None」的守卫**：`statistics.median` 对空列表抛 `StatisticsError`，
   本模块跑在端点上，抛就是 500。

文案禁令：本模块与其前端映射**一律不得**出现「已校准 / 经回测验证 / 预期年化 / 稳定盈利 / 保证」。
这是一张历史统计，不是收益承诺（G-10 用源码正则守门）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .attribution import _t_stat

_ROUND_USD = 4
_ROUND_RATIO = 4

# `travelToCostRatio` 的最小费率覆盖（低于它，那个比值的分母是 10 笔以下的子样本，没有意义）。
_MIN_FEE_COVERAGE = 0.05

POOLED_KEYS = ("roundTrips", "netPnlUsd", "grossPnlUsd", "feeUsd", "winRate", "avgWinUsd",
               "avgLossUsd", "payoff", "breakevenPayoff", "expectancyUsd", "expectancyR",
               "rSamples", "tStat", "medianHoldSeconds", "medianMoveBps", "medianCostBps",
               "travelToCostRatio", "feeCoverage", "feeTrips", "costBasisAssumedTrips")

# 置信度桶边界（与三档预设的 `min_confidence` 0.35 / 0.50 / 0.60 对齐，让「若改档会怎样」可读）。
CONFIDENCE_BUCKETS: tuple[tuple[float, float], ...] = (
    (0.0, 0.35), (0.35, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.0))

# 结论表（**冻结**；判据自上而下取首个命中，前端只读不复制）。
_VERDICTS: tuple[tuple[str, str], ...] = (
    ("insufficient_samples", "样本不足，不下结论"),
    ("no_edge_yet", "尚无正期望证据"),
    ("positive_not_significant", "有正期望迹象，但尚不显著"),
    ("negative_significant", "已实现负期望，且统计显著"),
    ("positive_significant", "已实现正期望，且统计显著"),
)
VERDICT_TITLES: dict[str, str] = dict(_VERDICTS)


@dataclass(frozen=True)
class VerdictGates:
    """结论判据的两个阈值（[P1-11] 形参聚合，满足 CLAUDE.md 的「≤5」）。"""

    min_trips: int = 30
    t_threshold: float = 2.0


def pooled(trips: list[dict]) -> dict:
    """池化盈利读数（约束 1/2/3）；空样本 ⇒ 全 None + `roundTrips=0`（**不造 0 冒充**）。"""
    total = len(trips)
    nets = [_finite(t.get("netPnlUsd")) or 0.0 for t in trips]
    wins = [v for v in nets if v > 0]
    losses = [v for v in nets if v <= 0]
    avg_win = (sum(wins) / len(wins)) if wins else None
    avg_loss = (sum(losses) / len(losses)) if losses else None
    win_rate = (len(wins) / total) if total else None
    r_values = [v for v in (_finite(t.get("rMultiple")) for t in trips) if v is not None]
    fee_rows = [t for t in trips if t.get("feeKnown")]
    fee_trips = len(fee_rows)
    fee_coverage = (fee_trips / total) if total else None
    median_move = _median([_finite(t.get("moveBps")) for t in trips])
    median_cost = _median([_finite(t.get("realizedCostBps")) for t in fee_rows])
    return {
        "roundTrips": total,
        "netPnlUsd": _round(sum(nets), _ROUND_USD) if total else None,
        "grossPnlUsd": _round(sum(_finite(t.get("grossPnlUsd")) or 0.0 for t in trips),
                              _ROUND_USD) if total else None,
        "feeUsd": _round(sum(_finite(t.get("roundTripFeeUsd")) or 0.0 for t in fee_rows),
                         _ROUND_USD) if fee_trips else None,
        "winRate": _round(win_rate, _ROUND_RATIO),
        "avgWinUsd": _round(avg_win, _ROUND_USD), "avgLossUsd": _round(avg_loss, _ROUND_USD),
        "payoff": _payoff(avg_win, avg_loss),
        "breakevenPayoff": _breakeven_payoff(win_rate),
        "expectancyUsd": _round(sum(nets) / total, _ROUND_USD) if total else None,
        "expectancyR": _round(sum(r_values) / len(r_values), _ROUND_RATIO) if r_values else None,
        "rSamples": len(r_values),
        "tStat": _t_stat(nets),
        "medianHoldSeconds": _median([_finite(t.get("holdSeconds")) for t in trips]),
        "medianMoveBps": median_move, "medianCostBps": median_cost,
        "travelToCostRatio": _travel_to_cost(median_move, median_cost, fee_coverage),
        "feeCoverage": _round(fee_coverage, _ROUND_RATIO), "feeTrips": fee_trips,
        "costBasisAssumedTrips": sum(1 for t in trips if t.get("costBasisAssumed")),
    }


def risk_profile(trips: list[dict]) -> dict:
    """R 分布 + 止损利用率（D-1 的那张表）。分母恒随读数同行下发（`rSamples` / `stopAtrCoverage`）。"""
    r_values = sorted(v for v in (_finite(t.get("rMultiple")) for t in trips) if v is not None)
    atr_mults = [v for v in (_finite(t.get("stopAtrMult")) for t in trips) if v is not None]
    total = len(trips)
    return {
        "rSamples": len(r_values),
        "rP10": _pct(r_values, 0.10), "rP50": _pct(r_values, 0.50), "rP90": _pct(r_values, 0.90),
        "reachedOneRShare": (_round(sum(1 for v in r_values if abs(v) >= 1.0) / len(r_values),
                                    _ROUND_RATIO) if r_values else None),
        "withinFifthRShare": (_round(sum(1 for v in r_values if abs(v) < 0.2) / len(r_values),
                                     _ROUND_RATIO) if r_values else None),
        "plannedRrMedian": _median([_finite(t.get("plannedRr")) for t in trips]),
        # 「典型交易走完了自己止损距离的百分之几」——D-1 的一句话结论就是这个数。
        "stopUtilizationP50": _median([_stop_utilization(t) for t in trips]),
        "medianStopAtrMult": _median(atr_mults),
        "stopAtrCoverage": _round(len(atr_mults) / total, _ROUND_RATIO) if total else None,
    }


def slice_by(trips: list[dict], key: str) -> list[dict]:
    """按某个维度分层（六个切片共用）；缺该维度的往返**不计入分母**（而不是计入并填 0）。"""
    buckets: dict[str, list[dict]] = {}
    for trip in trips:
        value = trip.get(key)
        if value is None or value == "":
            continue
        buckets.setdefault(str(value), []).append(trip)
    rows = []
    for name, rows_in in sorted(buckets.items()):
        stats = pooled(rows_in)
        rows.append({"key": name, "trips": stats["roundTrips"], "netPnlUsd": stats["netPnlUsd"],
                     "winRate": stats["winRate"], "expectancyUsd": stats["expectancyUsd"],
                     "medianRMultiple": _median([_finite(t.get("rMultiple")) for t in rows_in])})
    return rows


def slices(trips: list[dict]) -> dict:
    """六个分层切片（键名即 wire 键名）。`byConfidenceBucket` 走冻结桶边界，不按取值散列。"""
    return {
        "byBar": slice_by(trips, "bar"), "byInstId": slice_by(trips, "instId"),
        "byExitSource": slice_by(trips, "exitSource"), "byPreset": slice_by(trips, "preset"),
        "byHorizonProfile": slice_by(trips, "horizonProfile"),
        "byConfidenceBucket": slice_by([{**t, "confidenceBucket": _bucket_label(t)}
                                        for t in trips], "confidenceBucket"),
    }


def verdict(pooled_out: dict, gates: VerdictGates) -> dict:
    """冻结结论表（约束 5）：自上而下取首个命中，`tStat is None` 的两条分支**前置**。"""
    trips = int(pooled_out.get("roundTrips") or 0)
    expectancy = _finite(pooled_out.get("expectancyUsd"))
    t_stat = _finite(pooled_out.get("tStat"))
    code = _verdict_code(trips, expectancy, t_stat, gates)
    return {"code": code, "title": VERDICT_TITLES[code], "detail": _detail(pooled_out, code)}


def _verdict_code(trips: int, expectancy: float | None, t_stat: float | None,
                  gates: VerdictGates) -> str:
    if trips < int(gates.min_trips):
        return "insufficient_samples"
    value = 0.0 if expectancy is None else expectancy
    if t_stat is None:                                  # [P1-6] 前置：下面全是与阈值比大小
        return "positive_not_significant" if value > 0 else "no_edge_yet"
    threshold = float(gates.t_threshold)
    if value < 0 and t_stat <= -threshold:
        return "negative_significant"
    if value <= 0:
        return "no_edge_yet"
    return "positive_significant" if t_stat >= threshold else "positive_not_significant"


def _detail(pooled_out: dict, code: str) -> str:
    """结论的一句话（**复述分母**，约束 7）。文案禁令：不得出现任何收益承诺词。"""
    trips = int(pooled_out.get("roundTrips") or 0)
    if code == "insufficient_samples":
        return f"目前只有 {trips} 笔已完成往返，不足以对期望做任何判断；下列数字照常给出，仅供参考。"
    expectancy = pooled_out.get("expectancyUsd")
    t_stat = pooled_out.get("tStat")
    head = (f"{trips} 笔往返的净额期望为 {expectancy} USDT/笔"
            f"（t={'未知' if t_stat is None else t_stat}）")
    assumed = int(pooled_out.get("costBasisAssumedTrips") or 0)
    fee_trips = int(pooled_out.get("feeTrips") or 0)
    tail = f"；其中 {assumed} 笔的盈亏口径为推定、{fee_trips} 笔带费率记录。"
    return head + tail


def _bucket_label(trip: dict) -> str | None:
    """决策置信度所属的冻结桶；缺 `decisionConfidence` ⇒ None ⇒ 该笔不进这个切片的分母。"""
    value = _finite(trip.get("decisionConfidence"))
    if value is None:
        return None
    for low, high in CONFIDENCE_BUCKETS:
        if low <= value < high or (high >= 1.0 and value >= high):
            return f"{low:g}-{high:g}"
    return None


def _stop_utilization(trip: dict) -> float | None:
    """`|已实现位移| / 止损距离`；任一缺席 ⇒ None。`plannedRr` 与 `rMultiple` 都换算不出这个数。"""
    move = _finite(trip.get("moveBps"))
    stop = _finite(trip.get("stopBps"))
    if move is None or stop is None or stop <= 0:
        return None
    return move / stop


def _payoff(avg_win: float | None, avg_loss: float | None) -> float | None:
    if avg_win is None or avg_loss is None or avg_loss == 0:
        return None
    return _round(avg_win / abs(avg_loss), 3)


def _breakeven_payoff(win_rate: float | None) -> float | None:
    """该胜率下的盈亏平衡赔率；`winRate ∈ {0, 1}` ⇒ None（约束 2，不造 ∞）。"""
    if win_rate is None or win_rate <= 0 or win_rate >= 1:
        return None
    return _round((1 - win_rate) / win_rate, 3)


def _travel_to_cost(median_move: float | None, median_cost: float | None,
                    fee_coverage: float | None) -> float | None:
    """位移 / 成本（§零第 2 条那个数）；覆盖率不足或成本 ≤0 ⇒ None（约束 3）。"""
    if median_move is None or median_cost is None or median_cost <= 0:
        return None
    if fee_coverage is None or fee_coverage < _MIN_FEE_COVERAGE:
        return None
    return _round(median_move / median_cost, 3)


def _pct(rows: list[float], q: float) -> float | None:
    """分位数（线性插值）；空序列 ⇒ None。入参须已排序。"""
    if not rows:
        return None
    if len(rows) == 1:
        return _round(rows[0], _ROUND_RATIO)
    pos = q * (len(rows) - 1)
    low, high = math.floor(pos), math.ceil(pos)
    if low == high:
        return _round(rows[low], _ROUND_RATIO)
    return _round(rows[low] + (rows[high] - rows[low]) * (pos - low), _ROUND_RATIO)


def _median(values: list) -> float | None:
    """可得样本的中位数；一个都算不出 ⇒ None（约束 8：绝不让空列表把端点打成 500）。"""
    rows = sorted(float(v) for v in values if v is not None)
    if not rows:
        return None
    mid = len(rows) // 2
    out = rows[mid] if len(rows) % 2 else (rows[mid - 1] + rows[mid]) / 2
    return _round(out, _ROUND_RATIO)


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(float(value), digits)


def _finite(value) -> float | None:
    """有限数归一（**保留负号**）：净额为负是一个真实结论，绝不夹到 0。"""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None
