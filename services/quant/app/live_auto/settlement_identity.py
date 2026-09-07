"""本次持仓的稳定身份与观察；纯内存组件，不读取交易所或数据库。"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal


def finite_number(value) -> float | None:
    """拒绝 bool、复合对象和非有限数，未知值保持 None。"""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def canonical_milliseconds(value) -> str | None:
    """原始毫秒只接受正整数；不从截断秒值推测精度。"""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    if not text.isascii() or not text.isdecimal() or int(text) <= 0:
        return None
    return str(int(text))


def canonical_decimal(value) -> str:
    """稳定 key 用十进制数量，1 与 1.0 使用同一表达。"""
    return format(Decimal(str(value)).normalize(), "f")


def identity_hash(parts: list) -> str:
    """对规范 JSON 生成稳定、无个人明文的身份。"""
    canonical = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _camel(key: str) -> str:
    first, *rest = key.split("_")
    return first + "".join(part.title() for part in rest)


@dataclass(frozen=True)
class IdentityConfig:
    """装配会话的账户、run、标的和只读交易规格。"""

    account_id: object
    run_id: object
    inst_id: str
    instrument: dict = field(default_factory=dict)
    policy: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SettlementContext:
    """当前 epoch 的不可变快照；未取得的证据明确为 None。"""

    account_id: object = None
    run_id: object = None
    inst_id: str = ""
    epoch_key: str | None = None
    direction: str = ""
    margin_mode: str | None = None
    pos_side: str | None = None
    pos_id: str | None = None
    exchange_ctime_ms: str | None = None
    exchange_utime_ms: str | None = None
    c_time_s: float | None = None
    u_time_s: float | None = None
    observed_at_ts: float | None = None
    first_open_wall_ts: float | None = None
    time_basis: str = "legacy"
    total_open_qty: float = 0.0
    closed_qty: float = 0.0
    remaining_qty: float = 0.0
    entry_avg_px: float | None = None
    tick_sz: float | None = None
    booked_fee_usd: float | None = 0.0
    booked_close_pnl: float | None = 0.0
    booked_close_qty_price: float | None = 0.0
    fee_basis: str = "unknown"
    ownership_conflict: bool = False
    direction_confirmed: bool = False
    identity_basis: str = "unknown"
    schema_version: int = 1

    def wire(self) -> dict:
        """返回安全复制的 camelCase 完整内部断面。"""
        values = {_camel(key): value for key, value in asdict(self).items()}
        values["exchangeCTimeMs"] = values.pop("exchangeCtimeMs")
        values["exchangeUTimeMs"] = values.pop("exchangeUtimeMs")
        return values

    def observation_wire(self) -> dict:
        """只持久不可重建的观察；数量和费池仍由完整成交重建。"""
        keys = ("schemaVersion", "accountId", "runId", "instId", "epochKey",
                "posId", "posSide", "marginMode", "exchangeCTimeMs",
                "exchangeUTimeMs", "cTimeS", "uTimeS", "observedAtTs",
                "ownershipConflict", "directionConfirmed", "identityBasis")
        values = self.wire()
        return {key: values[key] for key in keys}


@dataclass(frozen=True)
class ObservationChange:
    """交给异步持久层的变化；revision 由 session 的事务维护。"""

    expected_epoch_key: str | None
    expected_settlement_revision: int | None
    context: SettlementContext | None


def _meta(trade: dict) -> dict:
    envelope = trade.get("explain") or {}
    if not envelope and trade.get("explain_json"):
        try:
            envelope = json.loads(trade["explain_json"])
        except (ValueError, TypeError):
            envelope = {}
    nested = envelope.get("meta", {}) if isinstance(envelope, dict) else {}
    return {**(nested if isinstance(nested, dict) else {}),
            **(trade.get("meta") or {})}


def _event(trade: dict) -> str | None:
    event = trade.get("accountingEventId") or _meta(trade).get(
        "accountingEventId")
    if isinstance(event, (str, int)) and not isinstance(event, bool):
        return str(event)
    identifier = trade.get("id", trade.get("tradeId"))
    if identifier is not None and not isinstance(identifier, bool):
        return f"trade:{identifier}"
    return None


def _fee(trade: dict) -> tuple[float | None, str]:
    # 同一经济核心解释来源，延迟导入避免 ai_auto.__init__ 的装配循环。
    from app.ai_auto.accounting_replay import leg_fee_evidence
    return leg_fee_evidence(trade)


def _add_known(left, right):
    return None if left is None or right is None else left + right


def _matches_remaining_position(after, remaining, direction):
    if after is None:
        return True
    if isinstance(after, str) and after in ("flat", "long", "short"):
        return after == ("flat" if remaining == 0 else direction)
    value = finite_number(after)
    if value is None:
        return False
    expected = remaining * (-1 if direction == "short" else 1)
    return math.isclose(value, expected, rel_tol=1e-9, abs_tol=1e-12)


class SettlementIdentityTracker:
    """单会话维护 epoch 与已落盘认领，绝不以 posId 充当黑名单。"""

    def __init__(self, context_config: IdentityConfig):
        self.config = context_config
        self._context: SettlementContext | None = None
        self._seen: set[str] = set()
        self.claimed: dict[str, dict] = {}

    def context(self) -> SettlementContext | None:
        """返回不可变的当前断面；结构全平后为空。"""
        return self._context

    def observation_wire(self) -> dict | None:
        """返回可供既有成员事务写入的观察。"""
        return self._context.observation_wire() if self._context else None

    def record_trade(self, trade: dict) -> None:
        """按稳定事件消费原始增量，未知费用不阻止可信全平边界。"""
        if trade.get("runId", trade.get("run_id", self.config.run_id)) != (
                self.config.run_id):
            return
        if trade.get("instId", self.config.inst_id) != self.config.inst_id:
            return
        event = _event(trade)
        if event is not None and event in self._seen:
            return
        if event is not None:
            self._seen.add(event)
        qty = finite_number(trade.get("qty"))
        price = finite_number(trade.get("price"))
        if qty is None or qty <= 0 or price is None or price <= 0:
            self.mark_conflict()
            return
        if trade.get("side") in ("long", "short"):
            self._record_open(trade, event, (qty, price))
        elif trade.get("side") == "close" and self._context:
            self._record_close(trade, qty, price)

    def _record_open(self, trade, event, amounts):
        qty, price = amounts
        if self._context and self._context.direction != trade["side"]:
            self.mark_conflict()
            return
        fee, basis = _fee(trade)
        wall = finite_number(trade.get("wallTs"))
        if wall is None or wall <= 0:
            wall = finite_number(_meta(trade).get("wallTs"))
        if self._context is None:
            config = self.config
            epoch = identity_hash([str(config.account_id), str(config.run_id),
                                   event]) if event is not None else None
            self._context = SettlementContext(
                account_id=config.account_id, run_id=config.run_id,
                inst_id=config.inst_id, epoch_key=epoch,
                direction=trade["side"],
                margin_mode=config.instrument.get("marginMode"),
                tick_sz=finite_number(config.instrument.get("tickSz")),
                first_open_wall_ts=wall if wall and wall > 0 else None,
                time_basis="wall" if wall and wall > 0 else "legacy",
                fee_basis=basis)
        current = self._context
        known_walls = [value for value in (current.first_open_wall_ts, wall)
                       if value is not None and value > 0]
        first_wall = min(known_walls) if known_walls else None
        total = current.total_open_qty + qty
        notional = (current.entry_avg_px or 0) * current.total_open_qty
        fee_basis = self._combined_basis(current.fee_basis, basis)
        self._context = replace(current, total_open_qty=total,
            remaining_qty=current.remaining_qty + qty,
            entry_avg_px=(notional + qty * price) / total,
            first_open_wall_ts=first_wall,
            time_basis="wall" if first_wall is not None else "legacy",
            booked_fee_usd=_add_known(current.booked_fee_usd, fee),
            fee_basis=fee_basis)

    @staticmethod
    def _combined_basis(left, right):
        if left not in ("measured", "estimated") or right not in (
                "measured", "estimated"):
            return "unknown"
        return "estimated" if "estimated" in (left, right) else "measured"

    def _record_close(self, trade, qty, price):
        current = self._context
        if qty - current.remaining_qty > max(qty, current.remaining_qty) * 1e-9:
            self.mark_conflict()
            return
        fee, basis = _fee(trade)
        remaining = current.remaining_qty - qty
        if abs(remaining) <= max(qty, current.remaining_qty) * 1e-9:
            remaining = 0.0
        if not _matches_remaining_position(
                trade.get("positionAfter"), remaining, current.direction):
            self.mark_conflict()
            return
        if remaining == 0:
            self._context = None
            return
        self._context = replace(current, remaining_qty=remaining,
            closed_qty=current.closed_qty + qty,
            booked_fee_usd=_add_known(current.booked_fee_usd, fee),
            booked_close_pnl=_add_known(current.booked_close_pnl,
                                       finite_number(trade.get("pnl"))),
            booked_close_qty_price=_add_known(
                current.booked_close_qty_price, qty * price),
            fee_basis=self._combined_basis(current.fee_basis, basis))

    def mark_conflict(self) -> ObservationChange | None:
        """本 epoch 的归属冲突不可由随后正常快照清除。"""
        current = self._context
        if current is None or current.ownership_conflict:
            return None
        self._context = replace(current, ownership_conflict=True)
        return ObservationChange(current.epoch_key, None, self._context)

    def observe_position(self, observation) -> ObservationChange | None:
        """采纳调用方已验证新鲜度的持仓快照，无新增网络请求。"""
        current = self._context
        if current is None or current.ownership_conflict:
            return None
        envelope = observation if isinstance(observation, dict) else {}
        if envelope.get("fresh") is False:
            return None
        if envelope.get("accountId", self.config.account_id) != (
                self.config.account_id):
            return self.mark_conflict()
        rows = (observation if isinstance(observation, list) else
                envelope.get("positions", [envelope]))
        candidates = [row for row in rows if isinstance(row, dict)
            and row.get("instId") == current.inst_id
            and finite_number(row.get("pos")) != 0]
        if not candidates:
            return None
        if len(candidates) != 1:
            return self.mark_conflict()
        position = candidates[0]
        if not self._matches_position(position):
            return self.mark_conflict()
        return self._observe(position, envelope.get("observedAtTs"))

    def _matches_position(self, position):
        current = self._context
        qty = finite_number(position.get("pos"))
        price = finite_number(position.get("avgPx"))
        side = position.get("posSide")
        if qty is None or price is None or price <= 0:
            return False
        if side not in ("long", "short", "net"):
            return False
        direction = side if side != "net" else (
            "long" if qty > 0 else "short")
        if direction != current.direction:
            return False
        if position.get("marginMode") != current.margin_mode:
            return False
        if not math.isclose(abs(qty), current.remaining_qty, rel_tol=1e-9):
            return False
        tolerance = max(current.tick_sz or 0,
                        abs(current.entry_avg_px) * 1e-8)
        return abs(price - current.entry_avg_px) <= tolerance

    def _observe(self, position, observed_at):
        current = self._context
        ctime = canonical_milliseconds(position.get("exchangeCTimeMs"))
        utime = canonical_milliseconds(position.get("exchangeUTimeMs"))
        wall = finite_number(observed_at or position.get("observedAtTs"))
        if not ctime or not utime or wall is None or wall <= 0:
            return None
        pos_id = position.get("posId")
        if not isinstance(pos_id, str) or not pos_id or int(utime) < int(ctime):
            return None
        if current.pos_id and (current.pos_id != pos_id or
                current.exchange_ctime_ms != ctime):
            return self.mark_conflict()
        if current.exchange_utime_ms and int(utime) < int(
                current.exchange_utime_ms):
            return None
        if current.observed_at_ts is not None and wall < current.observed_at_ts:
            return None
        self._context = replace(current, pos_id=pos_id,
            exchange_ctime_ms=ctime, exchange_utime_ms=utime,
            c_time_s=int(ctime) / 1000, u_time_s=int(utime) / 1000,
            pos_side=position["posSide"], observed_at_ts=wall,
            direction_confirmed=True, identity_basis="observed_position")
        if current == self._context:
            return None
        return ObservationChange(current.epoch_key, None, self._context)

    def restore(self, trades, persisted_observation=None) -> None:
        """在显示台账裁剪前重建完整 epoch、派生量与落库认领。"""
        self._context = None
        self._seen.clear()
        self.claimed.clear()
        ordered = sorted(enumerate(trades), key=lambda item: (
            finite_number(item[1].get("id", item[1].get("tradeId")))
            if finite_number(item[1].get("id", item[1].get("tradeId")))
            is not None else item[0]))
        for _, trade in ordered:
            previous = self._context
            self.record_trade(trade)
            self._restore_claim(trade, previous)
        self.adopt_observation(persisted_observation)

    def _restore_claim(self, trade, previous):
        if previous is None or self._context is not None:
            return
        if trade.get("side") != "close" or trade.get(
                "runId", trade.get("run_id", self.config.run_id)) != (
                    self.config.run_id):
            return
        meta = _meta(trade)
        validation = meta.get("settlementValidation") or {}
        if validation.get("schemaVersion") != 1 or validation.get(
                "status") != "matched":
            return
        if validation.get("epochKey") != previous.epoch_key:
            return
        self.claim({"settlementKey": meta.get("settlementKey") or
                    validation.get("settlementKey"),
                    "epochKey": validation.get("epochKey"),
                    "accountingEventId": meta.get("accountingEventId")})

    def claim(self, persisted_claim: dict) -> None:
        """仅由成交持久化成功或完整 DB 恢复调用。"""
        keys = ("settlementKey", "epochKey", "accountingEventId")
        if all(isinstance(persisted_claim.get(key), str)
               and persisted_claim[key] for key in keys):
            self.claimed[persisted_claim["settlementKey"]] = {
                key: persisted_claim[key] for key in keys}

    def adopt_observation(self, persisted) -> bool:
        """合并同 epoch 已落库观察，保留派生费池及粘滞冲突。"""
        current = self._context
        if not isinstance(persisted, dict) or current is None:
            return False
        scope = dict(schemaVersion=1, accountId=current.account_id,
                     runId=current.run_id, instId=current.inst_id,
                     epochKey=current.epoch_key,
                     marginMode=current.margin_mode)
        if any(persisted.get(key) != value for key, value in scope.items()):
            return False
        if current.pos_id and (
                persisted.get("posId") != current.pos_id or
                persisted.get("exchangeCTimeMs") != current.exchange_ctime_ms):
            self.mark_conflict()
            return False
        incoming_time = canonical_milliseconds(persisted.get("exchangeUTimeMs"))
        incoming_wall = finite_number(persisted.get("observedAtTs"))
        has_conflict = (current.ownership_conflict or
                        persisted.get("ownershipConflict") is True)
        if has_conflict:
            self._context = replace(current, ownership_conflict=True)
        if _is_older_observation(current, incoming_time, incoming_wall):
            return False
        self._context = replace(current,
            pos_id=persisted.get("posId"), pos_side=persisted.get("posSide"),
            exchange_ctime_ms=canonical_milliseconds(
                persisted.get("exchangeCTimeMs")),
            exchange_utime_ms=canonical_milliseconds(
                persisted.get("exchangeUTimeMs")),
            c_time_s=finite_number(persisted.get("cTimeS")),
            u_time_s=finite_number(persisted.get("uTimeS")),
            observed_at_ts=finite_number(persisted.get("observedAtTs")),
            ownership_conflict=has_conflict,
            direction_confirmed=persisted.get("directionConfirmed") is True,
            identity_basis="paused_position")
        return True


def _is_older_observation(current, incoming_time, incoming_wall):
    if current.exchange_utime_ms and (
            incoming_time is None or int(incoming_time) < int(
                current.exchange_utime_ms)):
        return True
    if current.observed_at_ts is not None and (
            incoming_wall is None or incoming_wall < current.observed_at_ts):
        return True
    return False


def closed_epoch_contexts(trades, config) -> list[SettlementContext]:
    """按结构终结顺序返回 close 前的完整断面，供旧暂停序号定位。"""
    from app.ai_auto.accounting_replay import TradeAccountingAccumulator

    tracker = SettlementIdentityTracker(config)
    accounting = TradeAccountingAccumulator(
        {"runId": config.run_id, "costBasis": "gross", "ctVal": 1})
    contexts = []
    for trade in _scoped_ordered_trades(trades, config):
        before = tracker.context()
        result = accounting.record_trade(trade)
        tracker.record_trade(trade)
        if (before is not None and result.get("complete")
                and not result.get("duplicate") and tracker.context() is None):
            contexts.append(before)
    return contexts


def _scoped_ordered_trades(trades, config):
    ordered = sorted(enumerate(trades), key=lambda item: (
        finite_number(item[1].get("id", item[1].get("tradeId")))
        if finite_number(item[1].get("id", item[1].get("tradeId")))
        is not None else item[0]))
    return [trade for _, trade in ordered if trade.get(
        "runId", trade.get("run_id", config.run_id)) == config.run_id
        and trade.get("instId", config.inst_id) == config.inst_id]


def has_closed_offline_epoch(trades, config, expected_epoch, pos_id) -> bool:
    """用完整 DB 原始腿核实旧离线事件终结，不升级其金额来源。

    Args:
        trades: 当前 run 全量 DB 成交。
        config: 会话固定归属。
        expected_epoch: 暂停契约的本地 epoch key。
        pos_id: 暂停契约的交易所 posId。

    Returns:
        旧 alias 与范围、墙钟、完整数量均成立且最终无未平仓。
    """
    from app.ai_auto.accounting_replay import TradeAccountingAccumulator

    if not expected_epoch or not isinstance(pos_id, str) or not pos_id:
        return False
    tracker = SettlementIdentityTracker(config)
    accounting = TradeAccountingAccumulator(
        {"runId": config.run_id, "costBasis": "gross", "ctVal": 1})
    found = False
    for trade in _scoped_ordered_trades(trades, config):
        before = tracker.context()
        result = accounting.record_trade(trade)
        tracker.record_trade(trade)
        if result.get("complete") and not result.get("duplicate"):
            found |= _legacy_close_matches(
                trade, before, expected_epoch, pos_id)
    return found and tracker.context() is None


def _legacy_close_matches(trade, before, expected_epoch, pos_id):
    if before is None or before.epoch_key != expected_epoch:
        return False
    meta = _meta(trade)
    wall = finite_number(trade.get("wallTs"))
    if wall is None:
        wall = finite_number(meta.get("wallTs"))
    if wall is None or wall <= 0 or wall != int(wall):
        return False
    opened = before.first_open_wall_ts
    if opened is None or wall < opened:
        return False
    expected = f"offline:{pos_id}:{int(wall)}"
    return meta.get("posId") == pos_id and _event(trade) == expected
