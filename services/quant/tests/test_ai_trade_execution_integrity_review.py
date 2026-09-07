"""最终审查发现的成交恢复边界回归，全部使用离线订单端口。"""
import pytest

from app.ai_auto.open_attempts import OpenAttemptManager
from app.okx.order_builder import OrderInput, SubmissionTrace
from tests.test_ai_trade_execution_receipts import _manager
from tests.test_live_executor import _make_executor


async def _persist(snapshot):
    return True


def _order_state(executor, *, phase, price, quantity):
    return {
        "instId": executor.inst_id, "side": "buy", "sz": 10,
        "clOrdId": "fixed", "ordId": "okx1", "state": phase,
        "avgPx": price, "accFillSz": quantity,
    }


async def _partial_open():
    executor, _ = _make_executor()
    manager = _manager(executor, _persist, "contrarian")
    assert await manager.prepare(OrderInput(
        executor.inst_id, "long", "limit", "cross", 100, 10, 3,
        None, None, "fixed", SubmissionTrace()))

    async def lookup(identity):
        assert identity == "fixed"
        return _order_state(executor, phase="partially_filled",
                            price=100, quantity=4)

    executor._get_order = lookup
    events = await manager.resolve_pending(executor, 1)
    assert len(events) == 1
    for event in events:
        manager.mark_events_persisted(
            event["data"]["explain"]["meta"]["accountingEventId"])
    return executor, manager


@pytest.mark.parametrize("phase", ["partially_filled", "filled", "canceled"])
@pytest.mark.parametrize("price", [20, 40, 1e308])
async def test_resting_retains_identity_when_incremental_price_is_invalid(
        phase, price):
    executor, manager = await _partial_open()
    assert await manager.complete_adoption(executor)
    resting = executor.resting
    events = await executor._advance_resting(resting, _order_state(
        executor, phase=phase, price=price, quantity=10), 2)
    assert not any(event["type"] == "trade" for event in events)
    assert executor.resting == resting
    assert executor.entry_qty_sz == 4
    assert executor.entry_price == 100
    assert manager.quota.used == 1


@pytest.mark.parametrize("price", [20, 40, 1e308, "unreadable"])
async def test_cold_pending_keeps_identity_until_incremental_price_is_valid(
        price):
    original, manager = await _partial_open()
    cold, _ = _make_executor()
    restored = OpenAttemptManager.restore(
        manager.runtime_payload(), account_id=1, run_id="run",
        inst_id=cold.inst_id, max_per_session=2,
        persist_callback=_persist)
    cold.open_attempts = restored
    cold.restore_trades(original.trades)
    cold.projected, cold.entry_price, cold.entry_qty_sz = "long", 100, 4

    async def lookup(identity):
        assert identity == "fixed"
        return _order_state(cold, phase="filled", price=price, quantity=10)

    cold._get_order = lookup
    assert await restored.resolve_pending(cold, 2) == []
    assert not await restored.complete_adoption(cold)
    assert restored.pending.cl_ord_id == "fixed"
    assert restored.pending.adopted_qty == 4
    assert restored.last_receipt.status == "accepted"
    assert cold.entry_qty_sz == 4


async def test_cancel_confirmation_cannot_discard_invalid_incremental_fill():
    executor, manager = await _partial_open()
    assert await manager.complete_adoption(executor)
    resting = executor.resting

    async def lookup(identity):
        return _order_state(executor, phase="canceled", price=20, quantity=10)

    executor._get_order = lookup
    outcome, events = await executor._confirm_open_canceled(
        resting, "reprice", 2)
    assert outcome == "unknown"
    assert not any(event["type"] == "trade" for event in events)
    assert executor.resting == resting
    assert executor.entry_qty_sz == 4
