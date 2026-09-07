"""运行期、历史与冷恢复共用的增量成交归并器；纯内存，无 IO。"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field

from .trade_economics import (TripPnlInputs, build_economics_snapshot,
                              finite, resolve_trip_economics)


def trade_meta(trade: dict) -> dict:
    """读取原始成交来源标记，兼容恢复后的 explain/meta 形状。"""
    envelope = trade.get("explain") or {}
    if not envelope and trade.get("explain_json"):
        try:
            envelope = json.loads(trade["explain_json"])
        except (ValueError, TypeError):
            envelope = {}
    nested = envelope.get("meta", {}) if isinstance(envelope, dict) else {}
    return {**(nested if isinstance(nested, dict) else {}),
            **(trade.get("meta") or {})}


def leg_fee_evidence(trade: dict) -> tuple[float | None, str]:
    """逐腿验证来源和币种；无来源的历史非空列不证明真实零费。"""
    meta = {**trade_meta(trade), **{key: trade[key] for key in
        ("feeBasis", "feeKnown", "feeCurrency") if key in trade}}
    value = finite(trade.get("fee"))
    basis = meta.get("feeBasis")
    if meta.get("feeKnown") is False or value is None:
        return None, "unknown"
    if meta.get("feeCurrency", "USDT") != "USDT":
        return None, "unknown"
    if basis in ("measured", "estimated"):
        return value, str(basis)
    if basis == "unknown":
        return None, "unknown"
    if (finite(meta.get("feeUsd")) is not None
            and finite(meta.get("feeBps")) is not None):
        return value, "estimated"
    return None, "legacy_unknown"


def accounting_event_key(trade: dict, run_id=None) -> tuple:
    """优先稳定事件身份；历史 id 只决定顺序，不使用 K 线时间去重。"""
    meta = trade_meta(trade)
    run = trade.get("runId", trade.get("run_id", run_id))
    event = trade.get("accountingEventId") or meta.get("accountingEventId")
    if event:
        return run, "event", str(event)
    identifier = trade.get("id", trade.get("tradeId"))
    return (run, "trade", identifier) if identifier is not None else (
        run, "dispatch", id(trade))


@dataclass
class _Position:
    run_id: object
    epoch_id: object
    side: str
    entry: dict
    remaining_qty: float = 0.0
    total_qty: float = 0.0
    entry_notional: float = 0.0
    remaining_fee: float | None = 0.0
    open_fee: float | None = 0.0
    close_fee: float | None = 0.0
    pieces: list[dict] = field(default_factory=list)
    sources: set[str] = field(default_factory=set)
    invalid: bool = False
    recorded_fee: float = 0.0
    exit_notional: float = 0.0


class TradeAccountingAccumulator:
    """按 run/epoch 唯一消费费池，派生统计容量不受 UI 环形缓存限制。"""

    def __init__(self, context: dict | None = None) -> None:
        self.context = dict(context or {})
        self._reset()

    def _reset(self) -> None:
        self._open: _Position | None = None
        self._seen: dict[tuple, dict] = {}
        self.completed_trips: list[dict] = []
        self.issues: list[str] = []
        self._session_fee = 0.0
        self._all_fees_known = True
        self._legacy = self.context.get("costBasis") not in ("net", "gross")
        self._unknown = 0
        self.last_result: dict | None = None

    @property
    def remaining_qty(self) -> float:
        """尚未由可信 close 增量消费的合约张数。"""
        return self._open.remaining_qty if self._open else 0.0

    @property
    def remaining_open_fee_usd(self) -> float | None:
        """当前剩余数量对应的开仓费；未知保持 None。"""
        return self._open.remaining_fee if self._open else 0.0

    @property
    def session_fee_usd(self) -> float | None:
        """包含未平腿的全 run 费用，任何一腿未知则整项未知。"""
        return self._session_fee if self._all_fees_known else None

    @property
    def current_epoch_id(self):
        """用于资金费覆盖对照的当前 epoch 身份。"""
        return self._open.epoch_id if self._open else None

    def restore(self, trades: list[dict], context: dict | None = None) -> dict:
        """按数据库 id 全量重建；重复调用先清派生状态，不产生副作用。"""
        if context is not None:
            self.context = dict(context)
        self._reset()
        ordered = sorted(enumerate(trades), key=lambda pair: (
            finite(pair[1].get("id", pair[1].get("tradeId")))
            if finite(pair[1].get("id", pair[1].get("tradeId"))) is not None
            else pair[0]))
        for _, trade in ordered:
            self.record_trade(trade)
        self.last_result = None
        return self.snapshot()

    def record_trade(self, trade: dict, epoch_id=None) -> dict:
        """采纳一条可信增量；重复事件不再改费池，坏覆盖返回原因。"""
        key = accounting_event_key(trade, self.context.get("runId"))
        if key in self._seen:
            self.last_result = {**self._seen[key], "duplicate": True}
            return self.last_result
        fee, source = leg_fee_evidence(trade)
        self._all_fees_known = self._all_fees_known and fee is not None
        self._session_fee += fee if fee is not None else 0.0
        self._legacy = self._legacy or source == "legacy_unknown"
        qty, price = finite(trade.get("qty")), finite(trade.get("price"))
        if qty is None or qty <= 0 or price is None or price <= 0:
            result = self._issue("invalid_quantity")
        elif trade.get("side") in ("long", "short"):
            result = self._record_open(
                trade, (qty, price, fee, source), epoch_id)
        elif trade.get("side") == "close":
            result = self._record_close(trade, (qty, price, fee, source))
        else:
            result = self._issue("invalid_side")
        result.update(eventKey=key, duplicate=False)
        self._seen[key] = result
        self.last_result = result
        return result

    def _issue(self, reason: str) -> dict:
        self.issues.append(reason)
        self._unknown += 1
        return {"complete": False, "coverage": "partial", "reason": reason}

    def _record_open(self, trade, amounts, epoch_id):
        qty, price, fee, source = amounts
        run = trade.get("runId", trade.get("run_id", self.context.get("runId")))
        meta = trade_meta(trade)
        epoch = meta.get("positionEpoch", epoch_id)
        if self._open is not None and (
            self._open.run_id != run or self._open.side != trade["side"]
            or (epoch is not None and self._open.epoch_id != epoch)):
            self._issue("position_epoch_changed")
            self._open = None
        if self._open is None:
            epoch = (epoch if epoch is not None
                     else accounting_event_key(trade, run))
            self._open = _Position(
                run, epoch, trade["side"], copy.deepcopy(trade))
        position = self._open
        position.remaining_qty += qty
        position.total_qty += qty
        position.entry_notional += qty * price
        position.remaining_fee = _add_known(position.remaining_fee, fee)
        position.open_fee = _add_known(position.open_fee, fee)
        position.recorded_fee += finite(trade.get("fee")) or 0.0
        position.sources.add(source)
        return {"complete": False, "coverage": "complete",
                "epochId": position.epoch_id, "opened": True}

    def _record_close(self, trade, amounts):
        qty, price, fee, source = amounts
        position = self._open
        if position is None:
            return self._issue("missing_open")
        run = trade.get("runId", trade.get("run_id", self.context.get("runId")))
        epoch = trade_meta(trade).get("positionEpoch")
        if run != position.run_id or (
                epoch is not None and epoch != position.epoch_id):
            return self._issue("position_epoch_changed")
        before = position.remaining_qty
        tolerance = max(before, qty) * 1e-9
        if qty - before > tolerance:
            position.invalid = True
            return self._issue("close_quantity_exceeded")
        final = math.isclose(qty, before, rel_tol=1e-9, abs_tol=0.0)
        allocation = position.remaining_fee
        if allocation is not None and not final:
            allocation *= qty / before
        position.remaining_fee = _subtract_known(
            position.remaining_fee, allocation)
        position.remaining_qty = 0.0 if final else before - qty
        position.close_fee = _add_known(position.close_fee, fee)
        position.exit_notional += qty * price
        position.recorded_fee += finite(trade.get("fee")) or 0.0
        position.sources.add(source)
        resolved = resolve_trip_economics(TripPnlInputs(
            self.context.get("costBasis"), trade.get("pnl"), allocation, fee))
        piece = {"grossPnlUsd": resolved.gross_pnl_usd,
                 "feeUsd": resolved.fee_usd,
                 "tradingNetUsd": resolved.trading_net_usd,
                 "recordedPnl": finite(trade.get("pnl"))}
        position.pieces.append(piece)
        if not _position_matches(trade, position):
            position.invalid = True
            return self._issue("position_quantity_mismatch")
        if not final:
            return {**piece, "complete": False, "epochId": position.epoch_id}
        self._open = None
        if position.invalid:
            return self._issue("position_quantity_mismatch")
        trip = self._complete(position, trade, price)
        self.completed_trips.append(trip)
        return {**trip, "complete": True}

    def _complete(self, position, trade, price):
        entry = position.entry_notional / position.total_qty
        multiplier = finite(self.context.get("ctVal"))
        totals = {key: _sum_pieces(position.pieces, key) for key in
                  ("grossPnlUsd", "feeUsd", "tradingNetUsd")}
        coverage = ("complete" if all(v is not None for v in totals.values())
                    else "partial")
        if ("legacy_unknown" in position.sources
                or self.context.get("costBasis") is None):
            coverage = "legacy_partial"
        return {**totals, "coverage": coverage, "epochId": position.epoch_id,
            "tripId": str(accounting_event_key(trade, position.run_id)),
            "entryPrice": entry,
            "exitPrice": position.exit_notional / position.total_qty,
            "qty": position.total_qty,
            "notionalUsd": position.entry_notional * multiplier
            if multiplier is not None and multiplier > 0 else None,
            "openFeeUsd": position.open_fee, "closeFeeUsd": position.close_fee,
            "recordedPnl": _sum_pieces(position.pieces, "recordedPnl"),
            "recordedFeeUsd": position.recorded_fee,
            "entryTrade": position.entry, "closeTrade": copy.deepcopy(trade),
            "side": position.side, "feeSources": sorted(position.sources),
            "accountingVersion": 2}

    def snapshot(self, context: dict | None = None) -> dict:
        """返回全 run 经济读数及每项覆盖，资金费由调用者传同 scope 证据。"""
        coverage = "legacy_partial" if self._legacy else (
            "partial" if self.issues else "complete")
        return build_economics_snapshot({**self.context, **(context or {}),
            "trips": self.completed_trips,
            "sessionFeeUsd": self.session_fee_usd,
            "unknownTripCount": self._unknown, "coverage": coverage})


def _position_matches(trade, position):
    after = trade.get("positionAfter")
    if after is None:
        return True
    if isinstance(after, str) and after in ("flat", "long", "short"):
        return after == ("flat" if position.remaining_qty == 0
                         else position.side)
    value = finite(after)
    if value is None:
        return False
    expected = position.remaining_qty * (-1 if position.side == "short" else 1)
    return math.isclose(value, expected, rel_tol=1e-9, abs_tol=1e-12)


def _add_known(left, right):
    return None if left is None or right is None else left + right


def _subtract_known(left, right):
    return None if left is None or right is None else left - right


def _sum_pieces(pieces, key):
    values = [piece[key] for piece in pieces]
    return None if any(v is None for v in values) else math.fsum(values)
