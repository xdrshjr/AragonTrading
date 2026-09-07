"""收益样本的金额、来源与总体资格；无数据库或运行期依赖。"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from .accounting_replay import trade_meta
from .trade_economics import finite

ProfitabilitySample = dict


@dataclass(frozen=True)
class ProfitabilityDataset:
    """同一次只读查询物化的往返、run维度及独立结构问题。"""

    samples: tuple[ProfitabilitySample, ...] = ()
    run_dimensions: tuple[dict, ...] = ()
    structural_issues: tuple[dict, ...] = ()
    open_epoch_count: int = 0
    as_of_ts: int = 0


def number(value) -> float | None:
    """有限经济数值；bool不是金额、置信度或版本。"""
    return None if isinstance(value, bool) else finite(value)


def text_dimension(value, default=None):
    """文本元数据不接受数值或容器，未知不会破坏JSON响应。"""
    return value if isinstance(value, str) and value.strip() else default


def _official_matches(close_meta: dict, totals: dict, key: str) -> bool:
    validation = close_meta.get("settlementValidation")
    if not isinstance(validation, dict):
        return False
    if (
        validation.get("status") != "matched"
        or validation.get("amountCoverage") != "complete"
        or close_meta.get("settlementSource") != "exchange"
    ):
        return False
    for name in ("epochKey", "settlementKey"):
        value = close_meta.get(name)
        if not isinstance(value, str) or not value.strip():
            return False
        if validation.get(name) != value:
            return False
    actual = number(totals.get(key))
    official_key = (
        "exchangeFeeUsd" if key == "feeUsd" else "exchangeGrossPnlUsd"
    )
    expected = number(close_meta.get(official_key))
    return (
        actual is not None
        and expected is not None
        and math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-8)
    )


def classify_fee_measurement(sources, close_meta: dict, totals: dict) -> str:
    """全腿来源或完整官方累计认证决定手续费是否实测。"""
    values = set(sources or ())
    if not values:
        return "unknown"
    if _official_matches(close_meta, totals, "feeUsd"):
        return "measured"
    if values and values == {"measured"}:
        return "measured"
    if values and values <= {"measured", "estimated"}:
        return "estimated"
    return "unknown"


def is_measured_net_evidence(sources, close_meta, totals, flags) -> bool:
    """费用与毛额都来自真实执行，局部估算须完整同源累计覆盖。"""
    if classify_fee_measurement(sources, close_meta, totals) != "measured":
        return False
    return not flags or (
        _official_matches(close_meta, totals, "feeUsd")
        and _official_matches(close_meta, totals, "grossPnlUsd")
    )


def _notional(trip, context):
    entry = number(trip.get("entryPrice"))
    amount = number(trip.get("notionalUsd"))
    if amount is not None and amount > 0:
        return amount, "contract"
    qty, multiplier = number(trip.get("qty")), number(context.get("ctVal"))
    if all(v is not None and v > 0 for v in (entry, qty, multiplier)):
        amount = number(entry * qty * multiplier)
        if amount is not None and amount > 0:
            return amount, "contract"
    exit_price, gross = (
        number(trip.get("exitPrice")),
        number(trip.get("grossPnlUsd")),
    )
    side = {"long": 1, "short": -1}.get(trip.get("side"))
    linear = str(context.get("instId", "")).endswith("-USDT-SWAP")
    if linear and entry and exit_price and gross and side:
        delta = side * (exit_price - entry)
        units = number(gross / delta) if delta else None
        if units is not None and units > 0:
            amount = number(entry * units)
            if amount is not None and amount > 0:
                return amount, "legacy_derived"
    return None, "unavailable"


def _wall_ts(trade):
    wall = number(trade_meta(trade).get("wallTs"))
    return wall if wall is not None and wall > 0 else number(trade.get("ts"))


def _ratio(value, denominator, scale=1):
    if value is None or denominator is None or denominator <= 0:
        return None
    return number(value / denominator * scale)


def _exclusion(trip, context, net, fee):
    if context.get("costBasis") not in ("net", "gross"):
        return "basis_unknown"
    if trip.get("coverage") != "complete" or fee is None:
        return "economics_incomplete"
    return "net_invalid" if net is None else None


def _metadata(trip):
    opened = trade_meta(trip.get("entryTrade", {}))
    closed = trip.get("closeMeta", trade_meta(trip.get("closeTrade", {})))
    frozen = opened.get("frozenEstimate")
    keys = {
        "estEdgeBps": "est_edge_bps",
        "estCostBps": "est_cost_bps",
        "estFeeBps": "est_fee_bps",
        "comparisonVersion": "comparison_version",
        "modelVersion": "model_version",
        "edgeSource": "edge_source",
        "costConfidence": "cost_confidence",
        "decisionConfidence": "decision_confidence",
        "wakeKind": "wake_kind",
    }
    if isinstance(frozen, dict):
        out = {key: frozen.get(old) for key, old in keys.items()}
        out["modelVersion"] = frozen.get("model_version", 2)
    elif "frozenEstimate" in opened:
        out = dict.fromkeys(keys)
    else:
        out = {key: opened.get(key, closed.get(key)) for key in keys}
    for key in ("modelVersion", "comparisonVersion"):
        if type(out[key]) is not int:
            out[key] = None
    for key in ("edgeSource", "costConfidence", "wakeKind"):
        out[key] = text_dimension(out[key])
    out["stopAtrMult"] = opened.get("stopAtrMult", closed.get("stopAtrMult"))
    return out, closed


def _risk_fields(trip, notional, metadata):
    entry, exit_price = (
        number(trip.get("entryPrice")),
        number(trip.get("exitPrice")),
    )
    first, last = trip.get("entryTrade", {}), trip.get("closeTrade", {})
    stop, take = (
        number(first.get("stopLossPrice")),
        number(first.get("takeProfitPrice")),
    )
    distance = abs(entry - stop) if entry and stop else None
    entry_ts, exit_ts = _wall_ts(first), _wall_ts(last)
    risk = _ratio(notional, entry)
    risk = number(risk * distance) if risk is not None and distance else None
    return {
        "entryTs": entry_ts,
        "exitTs": exit_ts,
        "moveBps": _ratio(abs(exit_price - entry), entry, 1e4)
        if entry and exit_price
        else None,
        "stopBps": _ratio(distance, entry, 1e4),
        "riskUsd": risk,
        "plannedRr": _ratio(abs(take - entry), distance)
        if take and entry
        else None,
        "stopAtrMult": number(metadata.get("stopAtrMult")),
        "holdSeconds": max(0, exit_ts - entry_ts)
        if entry_ts is not None and exit_ts is not None
        else None,
    }


def _economic_fields(trip, notional, gross, net, fee):
    """经济字段只投影归并器金额，展示元数据不参与计算。"""
    return {
        "entryPrice": number(trip.get("entryPrice")),
        "exitPrice": number(trip.get("exitPrice")),
        "qty": number(trip.get("qty")),
        "grossPnlUsd": gross,
        "netPnlUsd": net,
        "roundTripFeeUsd": fee,
        "feeKnown": fee is not None,
        "realizedGrossBps": _ratio(gross, notional, 1e4),
        "realizedNetBps": _ratio(net, notional, 1e4),
        "realizedFeeBps": _ratio(fee, notional, 1e4),
        "realizedCostBps": _ratio(fee, notional, 1e4),
    }


_SAMPLE_DIMENSIONS = (
    "runId", "instId", "bar", "preset", "horizonProfile", "costBasis",
)


def build_profitability_sample(
    trip: dict, context: dict
) -> ProfitabilitySample:
    """保留每个结构完整往返，派生量只取归并金额与冻结配置。"""
    net, fee = number(trip.get("tradingNetUsd")), number(trip.get("feeUsd"))
    gross = number(trip.get("grossPnlUsd"))
    notional, source = _notional(trip, context)
    metadata, closed = _metadata(trip)
    reason = _exclusion(trip, context, net, fee)
    flags = list(trip.get("closeEvidenceFlags", ()))
    measurement = classify_fee_measurement(trip.get("feeSources"), closed, trip)
    risk = _risk_fields(trip, notional, metadata)
    out = {
        **{key: context.get(key) for key in _SAMPLE_DIMENSIONS},
        **metadata,
        **risk,
        **_economic_fields(trip, notional, gross, net, fee),
        "tripId": str(trip["tripId"]),
        "side": trip.get("side"),
        "coverage": trip.get("coverage"),
        "netEligible": reason is None,
        "exclusionReason": reason,
        "flags": flags,
        "feeMeasurement": measurement,
        "notionalUsd": notional,
        "notionalSource": source,
        "rMultiple": _ratio(net, risk["riskUsd"]),
        "costErrorBps": None,
        "costBasisAssumed": context.get("costBasis") not in ("net", "gross"),
        "exitSource": text_dimension(closed.get("exitSource"), "unknown"),
        "accountingVersion": trip.get("accountingVersion"),
    }
    out["measuredNetEligible"] = reason is None and is_measured_net_evidence(
        trip.get("feeSources"), closed, trip, flags
    )
    for key in ("estEdgeBps", "estCostBps", "estFeeBps", "decisionConfidence"):
        out[key] = number(out[key])
    out["edgeErrorBps"] = _difference(
        out["realizedGrossBps"], out["estEdgeBps"]
    )
    out["costComparisonBasis"] = "fees"
    out["feeErrorBps"] = (
        _difference(out["realizedFeeBps"], out["estFeeBps"])
        if select_comparison_pairs((out,), "fees")
        else None
    )
    return out


def _difference(actual, expected):
    return (
        number(actual - expected)
        if actual is not None and expected is not None
        else None
    )


def has_fee_comparison_basis(sample: dict) -> bool:
    """新比较必须有明确模型、实测费用和合约名义；旧/未来版本不升级。"""
    return (
        sample.get("netEligible") is True
        and sample.get("notionalSource") == "contract"
        and sample.get("feeMeasurement") == "measured"
        and type(sample.get("comparisonVersion")) is int
        and sample["comparisonVersion"] == 1
        and type(sample.get("modelVersion")) is int
        and sample["modelVersion"] == 3
    )


def select_comparison_pairs(
    samples, kind: str
) -> tuple[ProfitabilitySample, ...]:
    """按单一资格选择同笔集合，缺失估计绝不由当前配置补齐。"""
    if kind not in ("edge", "fees", "confidence"):
        raise ValueError(f"未知收益比较类型：{kind}")
    out = []
    for sample in samples:
        if kind == "edge":
            valid = (
                sample.get("measuredNetEligible") is True
                and sample.get("notionalSource") == "contract"
                and number(sample.get("estEdgeBps")) is not None
                and number(sample.get("realizedGrossBps")) is not None
            )
        elif kind == "fees":
            valid = (
                has_fee_comparison_basis(sample)
                and number(sample.get("estFeeBps")) is not None
                and number(sample.get("realizedFeeBps")) is not None
            )
        else:
            value = number(sample.get("decisionConfidence"))
            valid = (
                sample.get("netEligible") is True
                and value is not None
                and 0 <= value <= 1
                and number(sample.get("netPnlUsd")) is not None
            )
        if valid:
            out.append(sample)
    return tuple(out)


def _coverage(samples, structural_issues=(), open_count=0):
    total = len(samples)
    eligible = sum(t.get("netEligible") is True for t in samples)
    fees = sum(t.get("feeKnown") is True for t in samples)
    reasons = Counter(
        t.get("exclusionReason") or "net_invalid"
        for t in samples
        if t.get("netEligible") is not True
    )
    return {
        "totalTrips": total,
        "netEligibleTrips": eligible,
        "measuredNetTrips": sum(
            t.get("measuredNetEligible") is True for t in samples
        ),
        "excludedTrips": total - eligible,
        "netCoverage": eligible / total if total else None,
        "feeTrips": fees,
        "feeCoverage": fees / total if total else None,
        "notionalKnownTrips": sum(
            t.get("notionalSource") == "contract" for t in samples
        ),
        "notionalDerivedTrips": sum(
            t.get("notionalSource") == "legacy_derived" for t in samples
        ),
        "structuralIssueCount": len(structural_issues),
        "openEpochCount": open_count,
        "excludedByReason": dict(sorted(reasons.items())),
    }


def summarize_coverage(dataset: ProfitabilityDataset) -> dict:
    """完整总体及匹配空run都可见，不从净额子集推定覆盖。"""
    out = _coverage(
        dataset.samples, dataset.structural_issues, dataset.open_epoch_count
    )
    symbols = sorted(
        {d.get("instId") or "unknown" for d in dataset.run_dimensions}
        | {s.get("instId") or "unknown" for s in dataset.samples}
    )
    by_symbol = []
    for symbol in symbols:
        runs = [
            d
            for d in dataset.run_dimensions
            if (d.get("instId") or "unknown") == symbol
        ]
        ids = {d.get("runId") for d in runs}
        rows = [
            s
            for s in dataset.samples
            if (s.get("instId") or "unknown") == symbol
        ]
        issues = [s for s in dataset.structural_issues if s.get("runId") in ids]
        by_symbol.append(
            {
                "instId": symbol,
                **_coverage(
                    rows, issues, sum(d.get("openEpochCount", 0) for d in runs)
                ),
            }
        )
    return {
        **out,
        "byInstId": by_symbol,
        "structuralIssues": list(dataset.structural_issues),
    }
