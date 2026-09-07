"""同笔、同分母的历史比较；描述值不用于交易门禁或资金分配。"""

from __future__ import annotations

from .profitability import CONFIDENCE_BUCKETS, _finite, _median, _round
from .profitability_sample import (
    has_fee_comparison_basis,
    select_comparison_pairs,
)


def calibration(
    trips: list[dict],
    *,
    bucket_min_trips: int = 10,
    model_constant_bps: float | None = None,
    structural_issue_count: int = 0,
) -> dict:
    """三块都从完整总体选择配对，结构缺口禁止整体结论。"""
    out = {
        "edgeNumerator": edge_numerator(
            trips, bucket_min_trips=bucket_min_trips
        ),
        "costModel": cost_model(
            trips,
            bucket_min_trips=bucket_min_trips,
            model_constant_bps=model_constant_bps,
        ),
        "confidenceReliability": confidence_reliability(
            trips, bucket_min_trips=bucket_min_trips
        ),
    }
    if structural_issue_count:
        for block in out.values():
            block["conclusive"] = False
        out["confidenceReliability"]["monotonic"] = None
    return out


def _complete(trips, paired_count, minimum):
    return (
        len(trips) == paired_count
        and paired_count >= int(minimum)
        and all(
            t.get("netEligible") is True
            and t.get("measuredNetEligible") is True
            for t in trips
        )
    )


def _coverage(count, total):
    return count / total if total else None


def edge_numerator(trips: list[dict], *, bucket_min_trips: int) -> dict:
    """冻结幅度与实测毛额在同一集合计算中位数和逐笔误差。"""
    rows = select_comparison_pairs(trips, "edge")
    signs = [
        int(_finite(t["realizedGrossBps"]) > 0)
        for t in rows
        if _finite(t["estEdgeBps"]) > 0
    ]
    return {
        "n": len(rows),
        "coverage": _coverage(len(rows), len(trips)),
        "medianEstEdgeBps": _median([_finite(t["estEdgeBps"]) for t in rows]),
        "medianRealizedGrossBps": _median(
            [_finite(t["realizedGrossBps"]) for t in rows]
        ),
        "medianErrorBps": _median(
            [
                _finite(t["realizedGrossBps"]) - _finite(t["estEdgeBps"])
                for t in rows
            ]
        ),
        "signSamples": len(signs),
        "signAgreementRate": _round(sum(signs) / len(signs), 4)
        if signs
        else None,
        "conclusive": _complete(trips, len(rows), bucket_min_trips),
    }


def cost_model(
    trips: list[dict],
    *,
    bucket_min_trips: int,
    model_constant_bps: float | None,
) -> dict:
    """只比较冻结手续费与同笔实测手续费；旧混比字段保持空。"""
    rows = select_comparison_pairs(trips, "fees")
    common = [t for t in trips if has_fee_comparison_basis(t)]
    return {
        "n": len(rows),
        "coverage": _coverage(len(rows), len(trips)),
        "comparisonBasis": "fees",
        "comparisonVersion": 1,
        "availableEstimateTrips": sum(
            _finite(t.get("estFeeBps")) is not None for t in common
        ),
        "availableRealizedTrips": sum(
            _finite(t.get("realizedFeeBps")) is not None for t in common
        ),
        "medianEstFeeBps": _median([_finite(t["estFeeBps"]) for t in rows]),
        "medianRealizedFeeBps": _median(
            [_finite(t["realizedFeeBps"]) for t in rows]
        ),
        "medianFeeErrorBps": _median(
            [
                _finite(t["realizedFeeBps"]) - _finite(t["estFeeBps"])
                for t in rows
            ]
        ),
        "medianEstTotalCostBps": _median(
            [_finite(t.get("estCostBps")) for t in trips]
        ),
        "medianEstCostBps": None,
        "medianRealizedCostBps": None,
        "medianErrorBps": None,
        "modelConstantBps": _round(model_constant_bps, 4),
        "conclusive": _complete(trips, len(rows), bucket_min_trips),
        "note": (
            f"{len(trips)} 笔总往返中 {len(rows)} 笔同口径配对；"
            "仅比较往返手续费，不含资金费、价差与冲击。旧总成本混比值不再提供；"
            "当前配置总成本预览不是历史预测。"
        ),
    }


def confidence_reliability(trips: list[dict], *, bucket_min_trips: int) -> dict:
    """五个固定置信桶都有足额实测总体证据后才判断区分度。"""
    rows = select_comparison_pairs(trips, "confidence")
    buckets = [
        _bucket_row(rows, low, high, bucket_min_trips)
        for low, high in CONFIDENCE_BUCKETS
    ]
    enough = _complete(trips, len(rows), bucket_min_trips) and all(
        b["trips"] >= bucket_min_trips for b in buckets
    )
    return {
        "n": len(rows),
        "coverage": _coverage(len(rows), len(trips)),
        "metadataMissingTrips": sum(
            (v := _finite(t.get("decisionConfidence"))) is None
            or not 0 <= v <= 1
            for t in trips
        ),
        "buckets": buckets,
        "monotonic": _monotonic(buckets) if enough else None,
        "conclusive": enough,
    }


def _bucket_row(rows, low, high, bucket_min_trips):
    inside = [
        t
        for t in rows
        if _in_bucket(_finite(t.get("decisionConfidence")), low, high)
    ]
    n = len(inside)
    enough = n >= int(bucket_min_trips)
    nets = [_finite(t["netPnlUsd"]) for t in inside]
    r_values = [_finite(t.get("rMultiple")) for t in inside]
    return {
        "key": f"{low:g}-{high:g}",
        "trips": n,
        "netSamples": len(nets),
        "rSamples": sum(v is not None for v in r_values),
        "winRate": _round(sum(v > 0 for v in nets) / n, 4)
        if n and enough
        else None,
        "expectancyUsd": _round(sum(nets) / n, 4) if n and enough else None,
        "medianRMultiple": _median(r_values) if enough else None,
    }


def _monotonic(buckets):
    rates = [b["winRate"] for b in buckets]
    if any(value is None for value in rates):
        return None
    return all(left <= right for left, right in zip(rates, rates[1:]))


def _in_bucket(value, low, high):
    return value is not None and (low <= value < high or value == high == 1)
