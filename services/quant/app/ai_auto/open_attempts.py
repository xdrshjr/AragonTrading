"""开仓身份恢复；复用 executor 查询及成交采纳，不重发不确定订单。"""
from __future__ import annotations

from copy import deepcopy
import asyncio
from dataclasses import asdict, dataclass, replace
import logging
import math
import time
from uuid import uuid4

from ..live_auto.execution_receipt import receipt_from_trace
from ..live_auto.resting import RestingOrder, build_resting, classify_state
from ..okx.order_builder import OrderInput, SubmissionTrace
from .entry_quota import EntryQuota


logger = logging.getLogger(__name__)
_ORDER_FIELDS = (
    "inst_id", "direction", "ord_type", "td_mode", "px", "sz", "lever",
    "tp_trigger_px", "sl_trigger_px",
)
_PENDING_KEYS = {
    "schema_version": "schemaVersion", "attempt_id": "attemptId",
    "account_id": "accountId", "run_id": "runId", "inst_id": "instId",
    "runtime_epoch_id": "runtimeEpochId", "cl_ord_id": "clOrdId",
    "ord_id": "ordId", "adopted_qty": "adoptedQty",
    "created_ts": "createdTs", "updated_ts": "updatedTs",
}


@dataclass
class PendingOpenAttempt:
    """发送前必须持久化的脱敏身份，确认接收与完成接管分开。"""

    attempt_id: str
    account_id: int
    run_id: str
    inst_id: str
    round: int
    runtime_epoch_id: str
    lane: str
    order: dict
    cl_ord_id: str
    ord_id: str | None = None
    phase: str = "not_sent"
    ack: str = "none"
    adopted_qty: float = 0.0
    estimate: dict | None = None
    created_ts: float = 0.0
    updated_ts: float = 0.0
    schema_version: int = 2

    def to_dict(self) -> dict:
        """转换为既有 runtimeIntegrity 的 JSON 契约。"""
        values = {_PENDING_KEYS.get(key, key): value
                  for key, value in asdict(self).items()}
        return deepcopy(getattr(self, "_unknown_fields", {})) | values

    @classmethod
    def from_dict(cls, snapshot: dict):
        """读取当前版本，未知结构抛错并由管理器保留原记录。"""
        if snapshot.get("schemaVersion") != 2:
            raise ValueError("pending schema 尚不可读")
        keys = {value: key for key, value in _PENDING_KEYS.items()}
        values = {keys.get(key, key): value
                  for key, value in snapshot.items()}
        known = {key: value for key, value in values.items()
                 if key in cls.__dataclass_fields__}
        pending = cls(**known)
        if not pending.attempt_id or not pending.cl_ord_id:
            raise ValueError("pending 缺少委托身份")
        pending._validate()
        pending._unknown_fields = {
            key: deepcopy(value) for key, value in snapshot.items()
            if keys.get(key, key) not in cls.__dataclass_fields__}
        return pending

    def _validate(self) -> None:
        if not isinstance(self.order, dict):
            raise ValueError("pending 委托结构不可读")
        if (self.order.get("inst_id") != self.inst_id
                or self.order.get("direction") not in {"long", "short"}
                or self.order.get("ord_type") not in {"market", "limit"}):
            raise ValueError("pending 委托方向或类型非法")
        quantity = float(self.order.get("sz"))
        self.adopted_qty = float(self.adopted_qty)
        if (not math.isfinite(quantity) or quantity <= 0
                or not math.isfinite(self.adopted_qty)
                or not 0 <= self.adopted_qty <= quantity):
            raise ValueError("pending 采纳量非法")
        if (self.phase not in {"not_sent", "sent"}
                or self.ack not in {"none", "accepted", "rejected"}
                or self.lane not in {"consensus", "contrarian"}):
            raise ValueError("pending 确认状态非法")


class OpenAttemptManager:
    """单成员恢复协调器；调用方必须持有成员执行锁。"""

    def __init__(self, *, account_id: int, run_id: str, inst_id: str,
                 quota: EntryQuota, persist_callback=None):
        self.account_id, self.run_id = account_id, run_id
        self.inst_id, self.quota = inst_id, quota
        self.persist_callback = persist_callback
        self.pending: PendingOpenAttempt | None = None
        self.last_receipt = None
        self.block_reason: str | None = None
        self._round = 0
        self._epoch = uuid4().hex
        self._lane = "consensus"
        self._estimate: dict | None = None
        self._extras: dict = {}
        self._unknown_pending: dict | None = None
        self._adoption_ready = False
        self._identities: dict[str, dict] = {}
        self._adopted: dict[str, float] = {}
        self._adopted_notional: dict[str, float] = {}
        self._pending_events: dict[str, dict] = {}

    def set_context(self, *, round: int, runtime_epoch_id: str,
                    lane: str = "consensus", estimate: dict | None = None):
        """绑定本轮冻结上下文，轮号不参与唯一身份生成。"""
        self._round, self._epoch = int(round), str(runtime_epoch_id)
        self._lane = "contrarian" if lane == "contrarian" else "consensus"
        self._estimate = deepcopy(estimate)

    @property
    def has_pending(self) -> bool:
        """未读懂的契约和未落库成交同样阻止新增开仓。"""
        queue = self._extras.get("pendingTrades")
        return (self.pending is not None or self._unknown_pending is not None
                or (queue is not None and queue != []))

    def runtime_payload(self) -> dict:
        """用于同一次 pending/quota/身份事务的完整片段。"""
        payload = deepcopy(self._extras)
        payload.update({
            "schemaVersion": 2, "entryQuota": self.quota.to_dict(),
            "pendingOpenAttempt": (
                self.pending.to_dict() if self.pending is not None
                else deepcopy(self._unknown_pending)),
        })
        return payload

    def set_pending_trades(self, trades: list[dict]) -> dict:
        """保留未落库原始成交，返回供所属会话原子写入的完整运行片段。"""
        self._extras["pendingTrades"] = deepcopy(trades)
        return self.runtime_payload()

    async def _persist(self, payload: dict | None = None) -> bool:
        if self.persist_callback is None:
            self.block_reason = "attempt_persist_failed"
            return False
        try:
            success = await asyncio.wait_for(self.persist_callback(
                payload if payload is not None else self.runtime_payload()),
                timeout=5.0)
        except Exception:
            logger.exception("开仓恢复契约持久化失败 run=%s", self.run_id)
            success = False
        self.block_reason = (
            None if success is True else "attempt_persist_failed")
        return success is True

    async def prepare(self, order: OrderInput) -> bool:
        """确认真实发送边界后，原子预写身份和可选逆势预留。"""
        queue = self._extras.get("pendingTrades")
        if queue is not None and queue != []:
            self.block_reason = "attempt_persist_failed"
            return False
        if self.has_pending:
            self.block_reason = "acceptance_unknown"
            return False
        attempt_id = f"{self.run_id}:{uuid4().hex}"
        if self._lane == "contrarian" and not self.quota.reserve(
                attempt_id, order.client_order_id or ""):
            self.block_reason = "entry_quota_exhausted"
            return False
        now = time.time()
        self.pending = PendingOpenAttempt(
            attempt_id, self.account_id, self.run_id, self.inst_id,
            self._round, self._epoch, self._lane,
            {key: getattr(order, key) for key in _ORDER_FIELDS},
            order.client_order_id or "", estimate=deepcopy(self._estimate),
            created_ts=now, updated_ts=now)
        try:
            persisted = await self._persist()
        except asyncio.CancelledError:
            await self.observe(SubmissionTrace(), reason="canceled_before_send")
            raise
        if not persisted:
            self.quota.release(attempt_id)
            self.pending = None
            return False
        self._adoption_ready = False
        return True

    async def observe(self, trace: SubmissionTrace, *,
                      reason: str = "pre_submit_error"):
        """先维护内存事实，再落确认状态；失败保留原恢复身份。"""
        pending = self.pending
        if pending is None:
            return None
        if pending.ack in {"accepted", "rejected"}:
            trace = SubmissionTrace(
                pending.phase, pending.ack, pending.ord_id)
        pending.phase, pending.ack = trace.phase, trace.ack
        pending.ord_id = trace.ord_id or pending.ord_id
        pending.updated_ts = time.time()
        self.last_receipt = receipt_from_trace(
            pending.attempt_id, pending.inst_id, pending.cl_ord_id,
            trace, reason)
        status = self.last_receipt.status
        if status == "accepted":
            self.quota.consume(pending.attempt_id)
        elif status in {"rejected", "not_submitted"}:
            self.quota.release(pending.attempt_id)
            snapshot = self.runtime_payload()
            snapshot["pendingOpenAttempt"] = None
            if await self._persist(snapshot):
                self.pending = None
            return self.last_receipt
        await self._persist()
        return self.last_receipt

    @classmethod
    def restore(cls, snapshot: dict | None, *, account_id: int,
                run_id: str, inst_id: str, max_per_session: int,
                persist_callback=None, legacy_used: int = 0):
        """恢复后未完成身份均视为可能已发送，不依据落盘 phase 放行。"""
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        quota = EntryQuota.restore(
            snapshot.get("entryQuota"), run_id=run_id,
            max_per_session=max_per_session, legacy_used=legacy_used)
        manager = cls(account_id=account_id, run_id=run_id,
                      inst_id=inst_id, quota=quota,
                      persist_callback=persist_callback)
        manager._extras = deepcopy(snapshot)
        raw = snapshot.get("pendingOpenAttempt")
        if isinstance(raw, dict):
            try:
                pending = PendingOpenAttempt.from_dict(raw)
                if (pending.account_id != account_id
                        or str(pending.run_id) != str(run_id)
                        or pending.inst_id != inst_id):
                    raise ValueError("pending 账户或运行身份不符")
                pending.phase = "sent"
                manager.pending = pending
                manager.last_receipt = receipt_from_trace(
                    pending.attempt_id, inst_id, pending.cl_ord_id,
                    SubmissionTrace("sent", pending.ack, pending.ord_id))
            except (ValueError, TypeError):
                manager._unknown_pending = deepcopy(raw)
                manager.block_reason = "acceptance_unknown"
        elif raw is not None:
            manager._unknown_pending = {"unreadable": deepcopy(raw)}
            manager.block_reason = "acceptance_unknown"
        return manager

    def restore_adopted(self, trades: list[dict]) -> None:
        """从完整已落库腿重建采纳量，不能信未提交的内存高水位。"""
        self._adopted.clear()
        self._adopted_notional.clear()
        self._identities.clear()
        self._pending_events.clear()
        for trade in trades:
            if trade.get("side") not in {"long", "short"}:
                continue
            meta = (trade.get("explain") or {}).get("meta") or {}
            identity = trade.get("clOrdId") or meta.get("clOrdId")
            if (not identity
                    or str(meta.get("runId", self.run_id)) != str(self.run_id)):
                continue
            quantity = float(trade.get("qty") or 0)
            self._adopted[identity] = self._adopted.get(identity, 0) + quantity
            self._adopted_notional[identity] = (
                self._adopted_notional.get(identity, 0)
                + quantity * float(trade.get("price") or 0))
            self._identities[identity] = dict(meta)
        if self.pending is not None:
            self.pending.adopted_qty = self._adopted.get(
                self.pending.cl_ord_id, 0.0)

    def _matches(self, pending: PendingOpenAttempt, state: dict) -> bool:
        if (not isinstance(state, dict)
                or state.get("instId") != pending.inst_id):
            return False
        phase = classify_state(state.get("state"))
        if phase == "unknown":
            return False
        if state.get("clOrdId") != pending.cl_ord_id or not state.get("ordId"):
            return False
        if pending.ord_id and state.get("ordId") != pending.ord_id:
            return False
        expected_side = (
            "buy" if pending.order["direction"] == "long" else "sell")
        if state.get("side") != expected_side:
            return False
        if state.get("accountId", self.account_id) != self.account_id:
            return False
        try:
            quantity = float(state.get("sz"))
            accumulated = float(state.get("accFillSz"))
        except (ValueError, TypeError):
            return False
        if phase == "filled" and not math.isclose(accumulated, quantity):
            return False
        return (math.isfinite(quantity) and math.isfinite(accumulated)
                and math.isclose(quantity, float(pending.order["sz"]))
                and 0 <= accumulated <= quantity
                and accumulated >= pending.adopted_qty
                and self._has_valid_incremental_price(
                    pending, state, accumulated))

    def _has_valid_incremental_price(self, pending, state,
                                     accumulated: float) -> bool:
        """正累计均价仍可能给出负增量价，采纳水位推进前核实名义差。"""
        delta = accumulated - pending.adopted_qty
        if delta <= 0:
            return True
        try:
            price = float(state.get("avgPx"))
        except (TypeError, ValueError, OverflowError):
            return False
        previous = (self._adopted_notional.get(pending.cl_ord_id)
                    if pending.adopted_qty > 0 else 0.0)
        if previous is None or not math.isfinite(previous) or previous < 0:
            return False
        incremental_price = (accumulated * price - previous) / delta
        return (math.isfinite(price) and price > 0
                and math.isfinite(incremental_price)
                and incremental_price > 0)

    async def resolve_pending(self, executor, ts: int) -> list[dict]:
        """按固定 ID 查询并增量采纳；未知/未找到不释放，不发 POST。"""
        pending = self.pending
        if pending is None:
            self.last_receipt = None
            return []
        if self._adoption_ready:
            return []
        if pending.ack == "rejected":
            await self.observe(SubmissionTrace("sent", "rejected"))
            return []
        try:
            state = await executor._get_order(pending.cl_ord_id)
        except Exception:
            self.block_reason = "acceptance_unknown"
            return []
        phase = (classify_state(state.get("state"))
                 if isinstance(state, dict) else "unknown")
        if not self._matches(pending, state) or phase == "unknown":
            self.block_reason = "acceptance_unknown"
            return []
        await self.observe(SubmissionTrace("sent", "accepted", state["ordId"]))
        return self._adopt_order(executor, state, ts, phase)

    def _adopt_order(self, executor, state: dict, ts: int,
                     phase: str) -> list[dict]:
        pending = self.pending
        accumulated = float(state.get("accFillSz", 0))
        delta = accumulated - pending.adopted_qty
        price = (float(state["avgPx"]) if delta > 0
                 else float(pending.order.get("px") or 0))
        order = build_resting(
            cl_ord_id=pending.cl_ord_id, ord_id=state["ordId"], intent="open",
            side=pending.order["direction"],
            px=pending.order.get("px") or price,
            sz=pending.order["sz"], strength=1.0, reason="recovered_open",
            now_ts=int(pending.created_ts),
            ttl_s=executor.settings.ai_trade_limit_order_ttl_s)
        order = replace(order, adopted_sz=pending.adopted_qty)
        events = []
        if delta > 0:
            events = executor._adopt_fill(
                order, price, accumulated, ts, delta_sz=delta)
            pending.adopted_qty = accumulated
        executor.resting = (replace(order, adopted_sz=accumulated)
                            if phase in {"live", "partial"} else None)
        self._adoption_ready = True
        return events

    async def complete_adoption(self, executor) -> bool:
        """仅在相应成交落库后调用；删除与挂单/持仓身份同事务。"""
        pending = self.pending
        if pending is None:
            return True
        if not self._adoption_ready or self._pending_events:
            return False
        pending.adopted_qty = self._adopted.get(
            pending.cl_ord_id, pending.adopted_qty)
        from dataclasses import asdict
        snapshot = self.runtime_payload()
        snapshot["pendingOpenAttempt"] = None
        snapshot["adoptedOrder"] = {
            "attemptId": pending.attempt_id, "clOrdId": pending.cl_ord_id,
            "ordId": pending.ord_id, "adoptedQty": pending.adopted_qty,
            "order": deepcopy(pending.order), "round": pending.round,
            "runtimeEpochId": pending.runtime_epoch_id, "lane": pending.lane,
            "estimate": deepcopy(pending.estimate),
            "resting": asdict(executor.resting) if executor.resting else None,
            "position": executor.position_payload(executor.entry_price),
        }
        if not await self._persist(snapshot):
            return False
        self._extras["adoptedOrder"] = snapshot["adoptedOrder"]
        previous = self._identities.get(pending.cl_ord_id, {})
        self._identities[pending.cl_ord_id] = (
            previous | self._identity_meta(pending))
        self.pending = None
        self._adoption_ready = False
        return True

    def _identity_meta(self, pending: PendingOpenAttempt) -> dict:
        return {
            "attemptId": pending.attempt_id, "runId": self.run_id,
            "accountId": self.account_id, "instId": self.inst_id,
            "runtimeEpochId": pending.runtime_epoch_id,
            "round": pending.round, "entryLane": pending.lane,
            "order": deepcopy(pending.order),
            "frozenEstimate": deepcopy(pending.estimate),
        }

    async def sync_execution(self, executor) -> bool:
        """成交落库后同步已转存挂单进度；写失败保持原身份供下轮重试。"""
        saved = self._extras.get("adoptedOrder")
        if self.has_pending or saved is None:
            return True
        if not isinstance(saved, dict) or self._pending_events:
            return False
        resting = executor.resting
        if resting is not None and resting.cl_ord_id != saved.get("clOrdId"):
            return False
        updated = deepcopy(saved)
        updated["resting"] = asdict(resting) if resting is not None else None
        updated["position"] = executor.position_payload(executor.entry_price)
        if updated == saved:
            return True
        snapshot = self.runtime_payload()
        snapshot["adoptedOrder"] = updated
        if not await self._persist(snapshot):
            return False
        self._extras["adoptedOrder"] = updated
        return True

    def restore_resting(self, executor) -> bool:
        """已转存挂单按原身份重新核查；有挂单不代表已经有持仓。"""
        if self._unknown_pending is not None:
            return False
        saved = self._extras.get("adoptedOrder")
        if self.pending is not None or saved is None:
            return True
        try:
            if not isinstance(saved, dict):
                raise ValueError("已接收挂单的契约不可读")
            raw = saved.get("resting")
            if raw is None:
                return True
            resting = RestingOrder(**raw)
            order = saved["order"]
            if (resting.intent != "open"
                    or resting.side not in {"long", "short"}
                    or not resting.cl_ord_id or not resting.ord_id
                    or order.get("inst_id") != self.inst_id
                    or order.get("direction") != resting.side
                    or not math.isfinite(resting.sz) or resting.sz <= 0):
                raise ValueError("已接收挂单的身份不完整")
            adopted = self._adopted.get(resting.cl_ord_id, 0.0)
            self.pending = PendingOpenAttempt(
                attempt_id=saved["attemptId"], account_id=self.account_id,
                run_id=self.run_id, inst_id=self.inst_id,
                round=saved.get("round", 0),
                runtime_epoch_id=saved.get("runtimeEpochId", "legacy"),
                lane=saved.get("lane", "consensus"), order=deepcopy(order),
                cl_ord_id=resting.cl_ord_id, ord_id=resting.ord_id,
                phase="sent", ack="accepted", adopted_qty=adopted,
                estimate=deepcopy(saved.get("estimate")),
                created_ts=resting.created_ts, updated_ts=time.time())
            executor.resting = replace(resting, adopted_sz=adopted)
            self.last_receipt = receipt_from_trace(
                self.pending.attempt_id, self.inst_id, resting.cl_ord_id,
                SubmissionTrace("sent", "accepted", resting.ord_id))
            return True
        except (KeyError, TypeError, ValueError):
            self._unknown_pending = {"adoptedOrder": deepcopy(saved)}
            self.block_reason = "acceptance_unknown"
            return False

    def decorate_trade(self, trade: dict) -> None:
        """成交携带固定事件身份和累计量，重复恢复可精确判增量。"""
        identity = trade.get("clOrdId")
        if trade.get("side") not in {"long", "short"} or not identity:
            return
        pending = self.pending
        meta = dict(self._identities.get(identity, {}))
        if pending is not None and pending.cl_ord_id == identity:
            meta.update(self._identity_meta(pending))
        self._adopted[identity] = self._adopted.get(identity, 0) + trade["qty"]
        self._adopted_notional[identity] = (
            self._adopted_notional.get(identity, 0)
            + trade["qty"] * float(trade.get("price") or 0))
        cumulative = self._adopted[identity]
        meta.update({
            "clOrdId": identity, "ordId": trade.get("ordId"),
            "adoptedQty": cumulative,
            "accountingEventId": f"{self.account_id}:{self.inst_id}:"
                                 f"{trade.get('ordId')}:{cumulative:.17g}",
        })
        self._identities[identity] = dict(meta)
        explain = trade.setdefault("explain", {}) or {}
        explain["meta"] = dict(explain.get("meta") or {}) | meta
        trade["explain"] = explain
        self._pending_events[meta["accountingEventId"]] = {
            "type": "trade", "data": trade}

    def pending_events(self) -> list[dict]:
        """返回已采纳但尚未确认落库的原事件，重试不再次采纳。"""
        return list(self._pending_events.values())

    def mark_events_persisted(self, accounting_event_id: str) -> None:
        """成交事务成功后幂等确认，确认前不准删除 pending。"""
        self._pending_events.pop(accounting_event_id, None)

    def adopted_notional(self, cl_ord_id: str) -> float | None:
        """历史已采纳名义（价格×张数），恢复不读新的实际累计均价。"""
        return self._adopted_notional.get(cl_ord_id)

    def order_details(self, cl_ord_id: str) -> dict:
        """取下单时冻结的类型和保护价格，避免成交价变化后重算。"""
        if self.pending is not None and self.pending.cl_ord_id == cl_ord_id:
            return self.pending.order
        return self._identities.get(cl_ord_id, {}).get("order", {})

    def matches_resting(self, resting: RestingOrder, state: dict) -> bool:
        """撤旧单的回读沿用同一身份及数量校验，避免取消剩余量后双开。"""
        pending = PendingOpenAttempt(
            attempt_id="resting", account_id=self.account_id,
            run_id=self.run_id, inst_id=self.inst_id, round=self._round,
            runtime_epoch_id=self._epoch, lane="consensus",
            order={"direction": resting.side, "sz": resting.sz},
            cl_ord_id=resting.cl_ord_id, ord_id=resting.ord_id,
            adopted_qty=resting.adopted_sz)
        return self._matches(pending, state)
