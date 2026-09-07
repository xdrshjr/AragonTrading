"""外部结算强身份校验与累计经济结果的增量换算，纯核心、无 IO。"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .settlement_identity import (
    SettlementContext, canonical_decimal, canonical_milliseconds,
    finite_number, identity_hash,
)


@dataclass(frozen=True)
class SettlementQuery:
    """本次提问的冻结持仓范围与真实墙钟窗口。"""

    inst_id: str
    direction: str
    entry_ts: int
    detect_ts: int
    context: SettlementContext | None = None
    tol_s: float = 90
    mode: str = "hot"
    paused_ts: float | None = None


@dataclass(frozen=True)
class SettlementFact:
    """仅由 validator 产生可采纳事实；所有金额已归一为支出为正。"""

    pos_id: str
    close_px: float
    gross_pnl_usd: float
    fee_usd: float
    funding_usd: float
    liq_usd: float
    close_ts: float
    settlement_key: str | None = None
    realized_pnl_usd: float | None = None
    settled_pnl_usd: float | None = None
    total_closed_qty: float | None = None
    open_avg_px: float | None = None
    close_ctime_ms: str | None = None
    close_utime_ms: str | None = None
    amount_sources: dict | None = None


@dataclass(frozen=True)
class SettlementValidation:
    """校验诊断；wire 隐藏内部 fact，不将未知金额冒充零。"""

    status: str = "pending"
    reason: str | None = None
    detail_reason: str | None = None
    message: str | None = None
    as_of_ts: float | None = None
    epoch_key: str | None = None
    settlement_key: str | None = None
    identity_basis: str = "unknown"
    amount_coverage: str = "unavailable"
    query_coverage: str = "single_page"
    price_basis: str = "unknown"
    fee_adjustment: dict | None = None
    fact: SettlementFact | None = None
    schema_version: int = 1

    def wire(self) -> dict:
        """返回 REST、WS、历史和 facts 共用的安全简报。"""
        return {
            "schemaVersion": self.schema_version, "status": self.status,
            "reason": self.reason, "detailReason": self.detail_reason,
            "message": self.message, "asOfTs": self.as_of_ts,
            "epochKey": self.epoch_key, "settlementKey": self.settlement_key,
            "identityBasis": self.identity_basis,
            "amountCoverage": self.amount_coverage,
            "queryCoverage": self.query_coverage,
            "priceBasis": self.price_basis,
            "feeAdjustment": dict(self.fee_adjustment)
            if self.fee_adjustment is not None else None}


@dataclass(frozen=True)
class SettlementIncrement:
    """同一实例提供最终成交与 realized 累加量，重试不重新核算。"""

    qty: float
    price: float
    pnl: float
    fee: float
    price_basis: str
    fee_basis: str
    fee_adjustment: dict
    accounting_event_id: str


@dataclass(frozen=True)
class IncrementResolution:
    """不可核验的残差保持 pending，不生成占位成交。"""

    status: str
    reason: str | None = None
    increment: SettlementIncrement | None = None


_AMOUNT_FIELDS = ("pnl", "fee", "fundingFee", "liqPenalty",
                  "settledPnl", "realizedPnl")
_REASON_PRIORITY = (
    "identity_unavailable", "ownership_conflict", "unsupported_product",
    "currency_unknown", "unsupported_currency", "not_closed",
    "identity_mismatch", "time_mismatch", "quantity_mismatch",
    "invalid_price", "missing_amount", "invalid_amount", "identity_failed",
)


def _context_reason(query):
    context = query.context
    if not isinstance(context, SettlementContext):
        return "identity_unavailable"
    if any(isinstance(value, bool) for value in (
            context.account_id, context.run_id, context.observed_at_ts)):
        return "identity_unavailable"
    if context.ownership_conflict:
        return "ownership_conflict"
    identities = (context.account_id, context.run_id, context.epoch_key,
                  context.pos_id, context.exchange_ctime_ms,
                  context.exchange_utime_ms)
    if any(value is None or value == "" for value in identities):
        return "identity_unavailable"
    if context.inst_id != query.inst_id or context.direction != query.direction:
        return "identity_mismatch"
    if context.direction not in ("long", "short"):
        return "identity_unavailable"
    return None


def _validation(query, now_ts, reason=None, **values):
    context = (query.context if isinstance(query.context, SettlementContext)
               else None)
    base = dict(as_of_ts=now_ts, reason=reason,
                epoch_key=context.epoch_key if context else None,
                identity_basis=context.identity_basis if context else "unknown")
    base.update(values)
    return SettlementValidation(**base)


def validate_settlement(rows, query, claimed, now_ts) -> SettlementValidation:
    """按固定优先级验证全部本次候选，拒绝歧义与冲突副本。"""
    reason = _context_reason(query)
    if reason:
        return _validation(query, now_ts, reason, status="unavailable")
    candidates = [row for row in (rows or []) if isinstance(row, dict)
                  and row.get("instId") == query.inst_id
                  and row.get("posId") == query.context.pos_id]
    valid, failures = {}, []
    duplicate_conflict = False
    claimed = claimed if isinstance(claimed, dict) else {}
    same_epoch_claims = []
    for row in candidates:
        fact, reason = _validate_row(row, query, now_ts)
        if reason:
            failures.append(reason)
            continue
        key = fact.settlement_key
        claim = claimed.get(key)
        if isinstance(claim, dict):
            if claim.get("epochKey") == query.context.epoch_key:
                same_epoch_claims.append(fact)
            continue
        content = _content_identity(row, fact)
        if key in valid and valid[key][1] != content:
            duplicate_conflict = True
        valid[key] = (fact, content)
    if duplicate_conflict:
        return _validation(query, now_ts, "conflicting_duplicate")
    return _choose_result((valid, same_epoch_claims, failures), query, now_ts)


def _choose_result(candidates, query, now_ts):
    valid, already, failures = candidates
    if len(valid) > 1:
        return _validation(query, now_ts, "ambiguous")
    if valid:
        fact = next(iter(valid.values()))[0]
        basis = ("exchange_aggregate_residual" if query.context.closed_qty
                 else "exchange_position_average")
        return _validation(query, now_ts, status="matched", fact=fact,
                           settlement_key=fact.settlement_key,
                           amount_coverage="complete", price_basis=basis)
    if already:
        fact = min(already, key=lambda item: item.settlement_key)
        return _validation(query, now_ts, "already_claimed",
                           status="already_claimed",
                           settlement_key=fact.settlement_key)
    reason = next((item for item in _REASON_PRIORITY if item in failures),
                  "not_found")
    return _validation(query, now_ts, reason,
                       amount_coverage="partial" if failures else "unavailable")


def _validate_row(row, query, now_ts):
    checks = (_product_reason, _ownership_reason, _time_reason,
              _quantity_price_reason, _amount_reason)
    for check in checks:
        reason = check(row, query, now_ts)
        if reason:
            return None, reason
    context = query.context
    ctime = canonical_milliseconds(row.get("exchangeCTimeMs"))
    utime = canonical_milliseconds(row.get("exchangeUTimeMs"))
    qty = finite_number(row.get("closeTotalPos"))
    key = identity_hash([
        str(context.account_id), query.inst_id, context.pos_id, ctime, utime,
        canonical_decimal(qty), context.direction, context.margin_mode])
    return SettlementFact(
        pos_id=context.pos_id, close_px=float(row["closeAvgPx"]),
        gross_pnl_usd=float(row["pnl"]), fee_usd=float(row["fee"]),
        funding_usd=float(row["fundingFee"]), liq_usd=float(row["liqPenalty"]),
        close_ts=int(utime) / 1000, settlement_key=key,
        realized_pnl_usd=float(row["realizedPnl"]),
        settled_pnl_usd=float(row["settledPnl"]), total_closed_qty=qty,
        open_avg_px=float(row["openAvgPx"]), close_ctime_ms=ctime,
        close_utime_ms=utime,
        amount_sources=dict(row.get("amountSources") or {})
    ), None


def _product_reason(row, query, now_ts):
    if row.get("instType") != "SWAP":
        return "unsupported_product"
    if not row.get("currency"):
        return "currency_unknown"
    if row.get("currency") != "USDT":
        return "unsupported_currency"
    if row.get("closed") is not True or str(row.get("type", "")) in (
            "1", "4", "5"):
        return "not_closed"
    return None


def _ownership_reason(row, query, now_ts):
    context = query.context
    mode = row.get("marginMode")
    if mode not in ("cross", "isolated") or context.margin_mode not in (
            "cross", "isolated"):
        return "identity_unavailable"
    if mode != context.margin_mode:
        return "identity_mismatch"
    ctime = canonical_milliseconds(row.get("exchangeCTimeMs"))
    if ctime is None:
        return "identity_unavailable"
    if ctime != context.exchange_ctime_ms:
        return "identity_mismatch"
    if context.pos_side not in ("long", "short", "net"):
        return "identity_unavailable"
    if row.get("posSide") and row["posSide"] != context.pos_side:
        return "identity_mismatch"
    direction = row.get("direction")
    if direction in ("long", "short"):
        return None if direction == context.direction else "identity_mismatch"
    if direction not in (None, "", "net") or not context.direction_confirmed:
        return "identity_unavailable"
    return None


def _time_reason(row, query, now_ts):
    context = query.context
    ctime = canonical_milliseconds(row.get("exchangeCTimeMs"))
    utime = canonical_milliseconds(row.get("exchangeUTimeMs"))
    observed = canonical_milliseconds(context.exchange_utime_ms)
    if not ctime or not utime or not observed:
        return "identity_unavailable"
    if int(utime) < int(ctime) or int(utime) <= int(observed):
        return "time_mismatch"
    close_ts = int(utime) / 1000
    tol = finite_number(query.tol_s)
    now = finite_number(now_ts)
    boundary = query.paused_ts if query.mode == "offline" else query.detect_ts
    boundary = finite_number(boundary)
    if (tol is None or tol < 0 or now is None
            or boundary is None or boundary <= 0):
        return "time_mismatch"
    lower = boundary - tol
    wall = finite_number(context.first_open_wall_ts)
    if wall is not None:
        lower = max(lower, wall - tol)
    if close_ts < lower or close_ts > now + tol:
        return "time_mismatch"
    return None


def _quantity_price_reason(row, query, now_ts):
    context = query.context
    qty = finite_number(row.get("closeTotalPos"))
    total = finite_number(context.total_open_qty)
    remaining = finite_number(context.remaining_qty)
    closed = finite_number(context.closed_qty)
    if any(value is None for value in (qty, total, remaining, closed)):
        return "quantity_mismatch"
    if qty <= 0 or remaining <= 0 or closed < 0:
        return "quantity_mismatch"
    if not math.isclose(qty, total, rel_tol=1e-9):
        return "quantity_mismatch"
    if not math.isclose(remaining, total - closed, rel_tol=1e-9):
        return "quantity_mismatch"
    prices = [finite_number(value) for value in (
        row.get("openAvgPx"), row.get("closeAvgPx"), context.entry_avg_px)]
    if any(value is None or value <= 0 for value in prices):
        return "invalid_price"
    tick = finite_number(context.tick_sz)
    tolerance = max(tick or 0, abs(prices[2]) * 1e-8)
    return "invalid_price" if abs(prices[0] - prices[2]) > tolerance else None


def _amount_reason(row, query, now_ts):
    invalid = row.get("invalidFields") or []
    values = [finite_number(row.get(key)) for key in _AMOUNT_FIELDS]
    for key, value in zip(_AMOUNT_FIELDS, values):
        raw = row.get(key)
        if key in invalid or (raw not in (None, "") and value is None):
            return "invalid_amount"
        if value is None:
            return "missing_amount"
    pnl, fee, funding, liq, settled, realized = values
    if settled != 0:
        return "invalid_amount"
    gap = realized - (pnl - fee - funding - liq + settled)
    tolerance = max(1e-8, 1e-6 * max(abs(value) for value in values))
    return "identity_failed" if abs(gap) > tolerance else None


def _content_identity(row, fact):
    numbers = [canonical_decimal(row[key]) for key in _AMOUNT_FIELDS]
    numbers.extend(canonical_decimal(row[key]) for key in (
        "openAvgPx", "closeAvgPx", "closeTotalPos"))
    return tuple(numbers) + (str(row.get("type", "")), row.get("marginMode"))


def match_settlement(rows, query, *, claimed, tol_s):
    """兼容旧签名；没有 context 时不恢复旧的弱时间窗匹配。"""
    current = replace(query, tol_s=tol_s)
    now = max(float(query.detect_ts), float(query.entry_ts))
    return validate_settlement(rows, current, claimed, now).fact


def resolve_settlement_increment(fact, context, cost_basis):
    """将官方累计量减去已记原始腿，负费用残差保持真实来源。"""
    if not isinstance(fact, SettlementFact) or not fact.settlement_key:
        return IncrementResolution("pending", "increment_invalid")
    amounts = _increment_amounts(fact, context, cost_basis)
    if amounts is None:
        return IncrementResolution("pending", "increment_invalid")
    qty, price, pnl, fee = amounts
    price_basis = ("exchange_aggregate_residual" if context.closed_qty > 0
                   else "exchange_position_average")
    adjustment = dict(kind="aggregate_residual",
                      exchangeTripFeeUsd=fact.fee_usd,
                      bookedLocalFeeUsd=context.booked_fee_usd,
                      residualFeeUsd=fee, feeBasis=context.fee_basis)
    event = (f"settlement:{context.run_id}:{context.epoch_key}:"
             f"{fact.settlement_key}")
    increment = SettlementIncrement(qty, price, pnl, fee, price_basis,
                                    context.fee_basis, adjustment, event)
    return IncrementResolution("matched", increment=increment)


def _increment_amounts(fact, context, cost_basis):
    if (not isinstance(context, SettlementContext)
            or cost_basis not in ("gross", "net")
            or context.fee_basis not in ("measured", "estimated")):
        return None
    fields = (context.total_open_qty, context.closed_qty, context.remaining_qty,
              context.booked_fee_usd, context.booked_close_pnl,
              context.booked_close_qty_price, fact.total_closed_qty,
              fact.close_px, fact.gross_pnl_usd, fact.fee_usd)
    if any(finite_number(value) is None for value in fields):
        return None
    qty = context.total_open_qty - context.closed_qty
    if qty <= 0 or not math.isclose(qty, context.remaining_qty, rel_tol=1e-9):
        return None
    if not math.isclose(fact.total_closed_qty, context.total_open_qty,
                        rel_tol=1e-9):
        return None
    price = ((fact.close_px * fact.total_closed_qty
              - context.booked_close_qty_price) / qty)
    total = fact.gross_pnl_usd - (fact.fee_usd if cost_basis == "net" else 0)
    pnl = total - context.booked_close_pnl
    fee = fact.fee_usd - context.booked_fee_usd
    if price <= 0 or not all(
            math.isfinite(value) for value in (price, pnl, fee)):
        return None
    return qty, price, pnl, fee

def settlement_meta_wire(fact, *, mark_px, waited_s,
                         booked_open_fee_usd, reject_reason=None) -> dict:
    """未舍入金额传给持久化和重放；未知证据不写占位金额。

    Args:
        fact: 已匹配事实或 None。
        mark_px: 消失侦测时的本地价格。
        waited_s: 已等待秒数。
        booked_open_fee_usd: 已记开仓费，保留带符号值。
        reject_reason: 本地回落的最后实际原因。

    Returns:
        explain.meta 的 camelCase 加法字段。
    """
    out = {"settlementSource": "exchange" if fact else "local_mark",
           "settlementWaitedS": int(max(0, waited_s))}
    mark = finite_number(mark_px)
    if mark is not None and mark > 0:
        out["markPxAtDetect"] = mark
    if reject_reason:
        out["settlementRejectReason"] = str(reject_reason)
    if fact is None:
        return out
    out.update(
        settlementPosId=fact.pos_id, settlementCloseTs=fact.close_ts,
        exchangeGrossPnlUsd=fact.gross_pnl_usd,
        exchangeFeeUsd=fact.fee_usd, exchangeFundingUsd=fact.funding_usd,
        exchangeLiqUsd=fact.liq_usd,
        exchangeNetAfterFundingUsd=(
            fact.gross_pnl_usd - fact.fee_usd - fact.funding_usd))
    if fact.realized_pnl_usd is not None:
        out["exchangeRealizedPnlUsd"] = fact.realized_pnl_usd
    if fact.settlement_key:
        out["settlementKey"] = fact.settlement_key
    booked = finite_number(booked_open_fee_usd)
    if booked is not None:
        out["bookedOpenFeeUsd"] = booked
    if mark is not None and mark > 0 and fact.close_px > 0:
        out["settlementPriceGapBps"] = round(
            (fact.close_px - mark) / mark * 1e4, 4)
    return out

def coverage_wire(exchange: int, local_mark: int) -> dict | None:
    """结算覆盖率 → `reconcile.settlementCoverage`；两者皆 0 ⇒ `None`（**整键省略**）。

    `N + M == 0` 时不写 `0` 也不写 `null`：「一次外部平仓都还没发生」与「发生了但一次都没拿到
    真值」是两件完全不同的事，用同一个 0 表示会让 `AC-1` 的分母失去意义。
    """
    total = int(exchange) + int(local_mark)
    if total <= 0:
        return None
    return {"exchange": int(exchange), "localMark": int(local_mark),
            "ratio": round(int(exchange) / total, 4)}
