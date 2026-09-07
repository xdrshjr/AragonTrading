"""带来源的双腿经济口径；纯计算，不读取配置或重写成交。"""
from __future__ import annotations

import math
from dataclasses import dataclass


def finite(value) -> float | None:
    """将边界数值规范为有限数，保留合法零与带符号返佣。"""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class TripPnlInputs:
    """已平数量对应的原始收益和两条腿费用，单位 USDT。"""

    pnl_basis: str | None
    recorded_close_pnl: float | None
    open_fee_usd: float | None
    close_fee_usd: float | None


@dataclass(frozen=True)
class TripEconomics:
    """未知派生为 None；complete 时毛额减费用等于交易净额。"""

    gross_pnl_usd: float | None
    fee_usd: float | None
    trading_net_usd: float | None
    coverage: str
    accounting_version: int = 2
    reason: str | None = None


def resolve_trip_economics(inputs: TripPnlInputs) -> TripEconomics:
    """解算单片双腿经济结果；非法输入降级，不抛出资金路径异常。"""
    pnl = finite(inputs.recorded_close_pnl)
    opened, closed = finite(inputs.open_fee_usd), finite(inputs.close_fee_usd)
    fee = None if opened is None or closed is None else opened + closed
    gross = net = None
    if inputs.pnl_basis == "net":
        net = pnl
        gross = pnl + fee if pnl is not None and fee is not None else None
    elif inputs.pnl_basis == "gross":
        gross = pnl
        net = pnl - fee if pnl is not None and fee is not None else None
    reason = ("missing_basis" if inputs.pnl_basis not in ("net", "gross")
              else "missing_pnl" if pnl is None
              else "missing_fee" if fee is None else None)
    coverage = "complete" if reason is None else "partial"
    if reason == "missing_basis":
        coverage = "legacy_partial"
    return TripEconomics(gross, fee, net, coverage, reason=reason)


def _sum_known(rows: list[dict], key: str) -> float | None:
    values = [finite(row.get(key)) for row in rows]
    if not values or any(value is None for value in values):
        return None
    return math.fsum(values)


def _sum_closed_members(rows: list[dict], key: str) -> float | None:
    """尚无闭合样本的成员不贡献金额；真实样本缺口仍传播未知。"""
    closed = [row for row in rows if row.get("roundTripCount")
              or row.get("unknownTripCount")]
    return _sum_known(closed, key)


def _metric(scope, basis, count, excluded, reason=None, **extra):
    return {"scope": scope, "basis": basis, "sampleCount": count,
            "excludedCount": excluded, "reason": reason, **extra}


def build_economics_snapshot(inputs: dict) -> dict:
    """由完整已平集合构建可空统计；同 scope 资金费才参与减算。"""
    trips = inputs.get("trips") or []
    scope = inputs.get("scope", "run")
    basis = inputs.get("basis", "local_estimate")
    values = [finite(t.get("tradingNetUsd")) for t in trips]
    known = [value for value in values if value is not None]
    wins = sum(value > 0 for value in known)
    gains = math.fsum(value for value in known if value > 0)
    losses = -math.fsum(value for value in known if value < 0)
    count, samples = len(trips), len(known)
    unknown = sum(t.get("coverage") != "complete" for t in trips)
    unknown += int(inputs.get("unknownTripCount", 0))
    coverage = inputs.get("coverage", "complete")
    if any(t.get("coverage") == "legacy_partial" for t in trips):
        coverage = "legacy_partial"
    elif unknown and coverage == "complete":
        coverage = "partial"
    common = _metric(scope, basis, samples, count - samples,
                     "no_samples" if not samples else None)
    metrics = {name: dict(common) for name in
               ("winRate", "avgPnlUsd", "profitFactor")}
    metrics["winRate"]["wins"] = wins
    metrics["avgPnlUsd"]["netSumUsd"] = math.fsum(known)
    metrics["profitFactor"].update(positivePnlUsd=gains, negativePnlUsd=losses)
    if not losses and samples:
        metrics["profitFactor"]["reason"] = "no_loss_denominator"
    for name in ("maxDrawdown", "sharpe"):
        metrics[name] = _metric("unavailable", basis, 0, count, "restore_gap")
    result = {"scope": scope, "basis": basis, "accountingVersion": 2,
        "asOfTs": inputs.get("asOfTs"), "roundTripCount": count,
        "completeFeeTripCount": sum(t.get("feeUsd") is not None
                                     for t in trips),
        "unknownTripCount": unknown, "coverage": coverage,
        "winRate": wins / samples if samples else None,
        "profitFactor": gains / losses if losses else None,
        "avgPnlUsd": math.fsum(known) / samples if samples else None,
        "sessionFeeUsd": finite(inputs.get("sessionFeeUsd")),
        "metricCoverage": metrics,
        "comparisonCoverage": inputs.get("comparisonCoverage") or {
            "comparable": False, "reason": "missing_identity"}}
    for key in ("grossPnlUsd", "feeUsd", "tradingNetUsd"):
        result[key] = _sum_known(trips, key)
        available = sum(finite(t.get(key)) is not None for t in trips)
        metrics[key] = _metric(scope, basis, available, count - available,
            "no_samples" if not count else
            "missing_fee" if result[key] is None else None)
    _append_funding(result, inputs)
    return result


def _append_funding(result: dict, inputs: dict) -> None:
    covered = inputs.get("fundingCoverage") == "complete"
    funding = finite(inputs.get("fundingCostUsd")) if covered else None
    net = result["tradingNetUsd"]
    result.update(fundingCostUsd=funding,
        fundingBasis=inputs.get("fundingBasis", "unknown")
        if funding is not None else "unknown",
        netAfterFundingUsd=net - funding
        if net is not None and funding is not None else None,
        openFundingAccruedUsd=finite(inputs.get("openFundingAccruedUsd")))
    comparison = inputs.get("comparisonCoverage") or {}
    for key in ("exchangeTradingNetUsd", "exchangeFundingCostUsd",
                "exchangeNetAfterFundingUsd", "exchangeRealizedPnlUsd",
                "liquidationCostUsd", "settlementAdjustmentUsd"):
        result[key] = (finite(inputs.get(key))
                       if comparison.get("comparable") else None)
    result["metricCoverage"]["fundingCostUsd"] = _metric(
        result["scope"], result["fundingBasis"],
        result["roundTripCount"] if covered else 0,
        0 if covered else result["roundTripCount"],
        None if covered else inputs.get("fundingReason", "restore_gap"))


def merge_economics_snapshots(snapshots: list[dict]) -> dict:
    """按成员分子分母池化组读数，绝不平均成员比例。"""
    rows = [row for row in snapshots if isinstance(row, dict)]
    result = build_economics_snapshot({"scope": "group", "trips": []})
    result["asOfTs"] = max((r.get("asOfTs") or 0 for r in rows), default=0)
    bases = {r.get("basis") for r in rows}
    result["basis"] = next(iter(bases)) if len(bases) == 1 else "mixed"
    for key in ("roundTripCount", "completeFeeTripCount", "unknownTripCount"):
        result[key] = sum(int(r.get(key) or 0) for r in rows)
    for key in ("grossPnlUsd", "feeUsd", "tradingNetUsd",
                "fundingCostUsd", "netAfterFundingUsd"):
        result[key] = _sum_closed_members(rows, key)
    for key in ("sessionFeeUsd", "openFundingAccruedUsd",
                "exchangeTradingNetUsd", "exchangeFundingCostUsd",
                "exchangeNetAfterFundingUsd", "exchangeRealizedPnlUsd",
                "liquidationCostUsd", "settlementAdjustmentUsd"):
        result[key] = _sum_known(rows, key)
    result["coverage"] = ("legacy_partial" if any(
        r.get("coverage") == "legacy_partial" for r in rows) else "complete"
        if rows and all(r.get("coverage") == "complete" for r in rows)
        else "partial")
    for key, numerator in (("winRate", "wins"), ("avgPnlUsd", "netSumUsd"),
                           ("profitFactor", "positivePnlUsd")):
        parts = [(r.get("metricCoverage") or {}).get(key, {}) for r in rows]
        samples = sum(int(p.get("sampleCount") or 0) for p in parts)
        excluded = sum(int(p.get("excludedCount") or 0) for p in parts)
        total = math.fsum(finite(p.get(numerator)) or 0 for p in parts)
        denom = (math.fsum(finite(p.get("negativePnlUsd")) or 0 for p in parts)
                 if key == "profitFactor" else samples)
        result[key] = total / denom if denom else None
        result["metricCoverage"][key] = _metric(
            "group", result["basis"], samples, excluded,
            None if denom else "no_loss_denominator"
            if key == "profitFactor" and samples else "no_samples",
            **{numerator: total})
        if key == "profitFactor":
            result["metricCoverage"][key]["negativePnlUsd"] = denom
    funding_bases = {r.get("fundingBasis") for r in rows}
    result["fundingBasis"] = (next(iter(funding_bases))
        if len(funding_bases) == 1 else "unknown")
    _merge_coverage(result, rows)
    return result


def _merge_coverage(result: dict, rows: list[dict]) -> None:
    """组金额沿用成员真实可用分母，未知成员不会变成完整零额。"""
    for key in ("grossPnlUsd", "feeUsd", "tradingNetUsd", "fundingCostUsd"):
        parts = [(row.get("metricCoverage") or {}).get(key, {}) for row in rows]
        samples = sum(int(part.get("sampleCount") or 0) for part in parts)
        excluded = sum(int(part.get("excludedCount") or 0) for part in parts)
        reason = None if result[key] is not None else next(
            (part["reason"] for part in parts if part.get("reason")
             and part.get("reason") != "no_samples"), "no_samples")
        basis = (result["fundingBasis"] if key == "fundingCostUsd"
                 else result["basis"])
        result["metricCoverage"][key] = _metric(
            "group", basis, samples, excluded, reason)
    comparisons = [row.get("comparisonCoverage") or {} for row in rows]
    comparable = bool(rows and all(
        part.get("comparable") for part in comparisons))
    result["comparisonCoverage"] = {"comparable": comparable,
        "scope": "group", "memberCount": len(rows),
        "coverage": "complete" if comparable else "partial",
        "reason": None if comparable else next(
            (part["reason"] for part in comparisons if part.get("reason")),
            "no_samples")}
    if not comparable:
        for key in ("exchangeTradingNetUsd", "exchangeFundingCostUsd",
                    "exchangeNetAfterFundingUsd", "exchangeRealizedPnlUsd",
                    "liquidationCostUsd", "settlementAdjustmentUsd"):
            result[key] = None
