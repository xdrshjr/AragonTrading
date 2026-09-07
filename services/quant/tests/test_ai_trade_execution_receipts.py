"""开仓身份和单次发送协议的离线回归。"""
import asyncio
from dataclasses import replace

import httpx
import pytest

from app.okx import order_builder, trading
from app.okx.private_client import OKXPrivateError
from app.okx.trade_client import OKXTradeClient
from tests.test_live_executor import _Sig, _make_executor


def test_internal_submission_contract_is_available():
    assert hasattr(order_builder, "SubmissionTrace")
    assert hasattr(trading, "new_client_order_id")
    ids = {trading.new_client_order_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(len(value) == 32 and value.isalnum() for value in ids)


@pytest.mark.parametrize("failure", ["transport", "429", "json"])
async def test_ai_order_never_reposts_uncertain_response(failure):
    requests = []

    async def respond(request):
        requests.append(request)
        if failure == "transport":
            raise httpx.ReadTimeout("响应丢失")
        if failure == "429":
            return httpx.Response(429, request=request)
        return httpx.Response(200, content=b"broken", request=request)

    client = OKXTradeClient("fake", "fake", "fake")
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://offline")

    async def get_client():
        return http

    async def noop(*args):
        pass

    client._get_client = get_client
    client._acquire_slot = noop
    client._invalidate_pooled = noop
    try:
        with pytest.raises((OKXPrivateError, ValueError)):
            await client.place_order({"clOrdId": "fixed"}, allow_retry=False)
        assert len(requests) == 1
    finally:
        await http.aclose()


async def test_dust_has_receipt_and_hold_clears_it():
    executor, calls = _make_executor(budget_usd=0.001)
    await executor.apply([_Sig("long")], ref_px=30000, bar_index=0, ts=1)
    assert executor.last_open_receipt.reason == "below_min_size"
    assert executor.last_open_receipt.submission_phase == "not_sent"
    assert not calls["orders"]
    await executor.apply([], ref_px=30000, bar_index=1, ts=2)
    assert executor.last_open_receipt is None


async def test_confirmed_ack_survives_later_failure():
    executor, _ = _make_executor()

    async def persist(snapshot):
        return True

    _manager(executor, persist)

    async def place(order):
        order.submission_trace.phase = "sent"
        order.submission_trace.ack = "accepted"
        order.submission_trace.ord_id = "okx1"
        raise RuntimeError("审计暂时不可用")

    executor._place_order = place
    with pytest.raises(RuntimeError):
        await executor.apply([_Sig("long")], ref_px=30000, bar_index=0, ts=1)
    assert executor.last_open_receipt.status == "accepted"
    assert executor.last_open_receipt.ord_id == "okx1"


@pytest.mark.parametrize("sent", [False, True])
async def test_cancellation_keeps_submission_truth(sent):
    executor, _ = _make_executor()

    async def persist(snapshot):
        return True

    _manager(executor, persist)

    async def place(order):
        if sent:
            order.submission_trace.phase = "sent"
        raise asyncio.CancelledError

    executor._place_order = place
    with pytest.raises(asyncio.CancelledError):
        await executor.apply([_Sig("long")], ref_px=30000, bar_index=0, ts=1)
    receipt = executor.last_open_receipt
    assert receipt.status == ("unknown" if sent else "not_submitted")
    assert receipt.reason == (
        "acceptance_unknown" if sent else "canceled_before_send")


def test_settlement_adjustment_keeps_signed_value_and_unknown():
    row = trading._normalize_position_history({
        "pnl": "10", "fee": "-1", "fundingFee": "-.3",
        "liqPenalty": "-.4", "settledPnl": ".2", "realizedPnl": "8.5",
    })
    assert row["settledPnl"] == .2
    assert row["identityGapUsd"] == pytest.approx(0)
    assert trading._normalize_position_history({})["settledPnl"] is None


def _manager(executor, persist, lane="consensus"):
    import importlib.util
    assert importlib.util.find_spec("app.ai_auto.open_attempts") is not None
    from app.ai_auto.entry_quota import EntryQuota
    from app.ai_auto.open_attempts import OpenAttemptManager
    manager = OpenAttemptManager(
        account_id=1, run_id="run", inst_id=executor.inst_id,
        quota=EntryQuota("run", 2), persist_callback=persist)
    manager.set_context(round=1, runtime_epoch_id="epoch", lane=lane)
    executor.open_attempts = manager
    return manager


@pytest.mark.parametrize("lane", ["consensus", "contrarian"])
async def test_every_lane_persists_before_post_and_blocks_storage_failure(lane):
    executor, calls = _make_executor()
    snapshots = []

    async def persist(snapshot):
        snapshots.append(snapshot)
        return False

    manager = _manager(executor, persist, lane)
    await executor.apply([_Sig("long")], ref_px=30000, bar_index=0, ts=1)
    assert not calls["orders"]
    assert executor.last_open_receipt.reason == "attempt_persist_failed"
    assert snapshots[0]["pendingOpenAttempt"]["clOrdId"]
    assert manager.quota.reserved == 0


@pytest.mark.parametrize("lane", ["consensus", "contrarian"])
async def test_unknown_keeps_identity_no_second_post_and_query_adopts_once(lane):
    executor, calls = _make_executor()
    snapshots = []

    async def persist(snapshot):
        snapshots.append(snapshot)
        return True

    manager = _manager(executor, persist, lane)

    async def place(order):
        assert snapshots[-1]["pendingOpenAttempt"]["clOrdId"] == order.client_order_id
        calls["orders"].append(order)
        order.submission_trace.phase = "sent"
        raise OKXPrivateError("OKX_UNREACHABLE", "响应丢失")

    executor._place_order = place
    await executor.apply([_Sig("long")], ref_px=30000, bar_index=0, ts=1)
    pending = manager.pending
    assert pending is not None
    assert manager.quota.reserved == (1 if lane == "contrarian" else 0)
    await executor.apply([_Sig("long")], ref_px=30000, bar_index=1, ts=2)
    assert len(calls["orders"]) == 1

    async def lookup(identity):
        assert identity == pending.cl_ord_id
        return {
            "instId": executor.inst_id, "clOrdId": identity, "ordId": "okx1",
            "side": "buy", "sz": 10, "state": "filled",
            "avgPx": 29900, "accFillSz": 10,
        }

    executor._get_order = lookup
    events = await manager.resolve_pending(executor, 3)
    assert len([event for event in events if event["type"] == "trade"]) == 1
    assert executor.entry_price == 29900
    assert manager.last_receipt.status == "accepted"
    assert manager.pending is not None  # 成交落库确认前不能删恢复记录
    assert await manager.resolve_pending(executor, 4) == []
    assert executor.trade_count == 1
    assert manager.quota.used == (1 if lane == "contrarian" else 0)
    for event in events:
        if event["type"] == "trade":
            manager.mark_events_persisted(
                event["data"]["explain"]["meta"]["accountingEventId"])
    assert await manager.complete_adoption(executor)
    assert manager.pending is None


async def test_cold_not_sent_record_is_uncertain_and_not_found_cannot_release():
    executor, _ = _make_executor()
    snapshots = []

    async def persist(snapshot):
        snapshots.append(snapshot)
        return True

    manager = _manager(executor, persist, "contrarian")
    order = order_builder.OrderInput(
        executor.inst_id, "long", "market", "cross", None, 10, 3, None, None,
        trading.new_client_order_id(), order_builder.SubmissionTrace())
    assert await manager.prepare(order)
    restored = type(manager).restore(
        manager.runtime_payload(), account_id=1, run_id="run",
        inst_id=executor.inst_id, max_per_session=2, persist_callback=persist)

    async def lookup(identity):
        return {}

    executor._get_order = lookup
    assert await restored.resolve_pending(executor, 3) == []
    assert restored.pending is not None
    assert restored.quota.reserved == 1
    assert restored.last_receipt.status == "unknown"


@pytest.mark.parametrize("phase", ["filled", "canceled"])
async def test_partial_then_terminal_uses_incremental_notional_and_quantity(phase):
    from app.live_auto.resting import build_resting
    executor, _ = _make_executor()
    executor.resting = build_resting(
        cl_ord_id="fixed", ord_id="okx1", intent="open", side="long",
        px=100, sz=10, strength=1, reason="测试", now_ts=1, ttl_s=100)
    await executor._advance_resting(executor.resting, {
        "state": "partially_filled", "avgPx": 100, "accFillSz": 4,
    }, 2)
    await executor._advance_resting(executor.resting, {
        "state": phase, "avgPx": 106, "accFillSz": 10,
    }, 3)
    assert [trade["qty"] for trade in executor.trades] == [4, 6]
    assert [trade["price"] for trade in executor.trades] == [100, 110]
    assert executor.entry_price == 106
    assert executor.entry_qty_sz == 10
    assert executor.resting is None


@pytest.mark.parametrize("outcome,ack,phase", [
    ("accepted", "accepted", "sent"),
    ("audit", "accepted", "sent"),
    ("close", "accepted", "sent"),
    ("51008", "rejected", "sent"),
    ("51004", "rejected", "sent"),
    ("51000", "rejected", "sent"),
    ("51001", "rejected", "sent"),
    ("51010", "rejected", "sent"),
    ("51006", "rejected", "sent"),
    ("51121", "rejected", "sent"),
    ("51603", "none", "sent"),
    ("OKX_HTTP_ERROR", "none", "sent"),
    ("empty", "none", "sent"),
    ("leverage", "none", "not_sent"),
])
async def test_trading_sets_trace_before_cleanup_and_only_known_rejections(
        monkeypatch, outcome, ack, phase):
    from tests.test_trading_api import _FakeRest, _FakeTradeClient
    executor, _ = _make_executor()
    requests = []
    executor.settings.live_max_order_notional_usd = 100000
    trace = order_builder.SubmissionTrace()

    class Client(_FakeTradeClient):
        async def place_order(self, payload, *, allow_retry=None):
            assert trace.phase == "sent"
            assert allow_retry is False
            requests.append(payload)
            if outcome in {"accepted", "audit", "close"}:
                return {"ordId": "okx1", "clOrdId": payload["clOrdId"]}
            if outcome == "empty":
                return {}
            raise OKXPrivateError(outcome, "测试拒绝")

        async def set_leverage(self, *args, **kwargs):
            if outcome == "leverage":
                raise OKXPrivateError("51008", "预检失败")

        async def aclose(self):
            if outcome == "close":
                raise RuntimeError("关闭失败")

    async def audit(*args, **kwargs):
        if outcome == "audit":
            assert trace.ack == "accepted"
            raise RuntimeError("审计失败")

    monkeypatch.setattr(trading, "OKXTradeClient", Client)
    monkeypatch.setattr(trading, "get_rest_client", lambda: _FakeRest())
    monkeypatch.setattr(trading.repo, "create_live_order_audit", audit)
    order = order_builder.OrderInput(
        executor.inst_id, "long", "limit", "cross", 30000, 10, 3,
        None, None, trading.new_client_order_id(), trace)
    try:
        await trading.place_live_order(None, executor.creds, 1, order,
                                       settings=executor.settings)
    except (OKXPrivateError, RuntimeError):
        pass
    assert trace.ack == ack
    assert trace.phase == phase
    assert len(requests) == (0 if outcome == "leverage" else 1)
    if requests:
        assert requests[0]["clOrdId"] == order.client_order_id
        assert "submission_trace" not in requests[0]


async def test_receipt_remains_confirmed_when_later_observation_disagrees():
    executor, _ = _make_executor()

    async def persist(snapshot):
        return True

    manager = _manager(executor, persist)
    order = order_builder.OrderInput(
        executor.inst_id, "long", "market", "cross", None, 10, 3,
        None, None, trading.new_client_order_id(), order_builder.SubmissionTrace())
    assert await manager.prepare(order)
    await manager.observe(order_builder.SubmissionTrace("sent", "accepted", "okx1"))
    await manager.observe(order_builder.SubmissionTrace("sent", "rejected"))
    assert manager.last_receipt.status == "accepted"
    assert manager.pending.ord_id == "okx1"


def test_restore_adopted_is_full_replay_and_does_not_double_highwater():
    executor, _ = _make_executor()
    manager = _manager(executor, None)
    rows = [{"side": "long", "qty": 4, "clOrdId": "fixed"}]
    manager.restore_adopted(rows)
    manager.restore_adopted(rows)
    assert manager._adopted["fixed"] == 4


async def test_failed_trade_persistence_retains_events_and_prevents_completion():
    executor, _ = _make_executor()

    async def persist(snapshot):
        return True

    manager = _manager(executor, persist)
    assert hasattr(manager, "pending_events")
    trade = {"side": "long", "qty": 4, "clOrdId": "fixed", "ordId": "okx1"}
    manager.decorate_trade(trade)
    event_id = trade["explain"]["meta"]["accountingEventId"]
    assert manager.pending_events()[0]["data"] == trade
    manager.mark_events_persisted(event_id)
    manager.mark_events_persisted(event_id)
    assert manager.pending_events() == []


@pytest.mark.parametrize("kind,closed", [
    ("1", False), ("2", True), ("3", True), ("4", False),
    ("5", False), ("6", True), ("", None),
])
def test_position_history_keeps_authoritative_closed_and_currency(kind, closed):
    row = trading._normalize_position_history({
        "instId": "BTC-USDT-SWAP", "instType": "SWAP", "ccy": "USDT",
        "type": kind, "uTime": "1000000", "cTime": "999000",
    })
    assert row["closed"] is closed
    assert row["currency"] == "USDT"
    assert trading._normalize_position_history({"ccy": "BTC"})["currency"] is None


async def test_cancellation_during_prepare_releases_only_unsent_reservation():
    executor, calls = _make_executor()
    entered = asyncio.Event()
    snapshots = []

    async def persist(snapshot):
        snapshots.append(snapshot)
        if len(snapshots) == 1:
            entered.set()
            await asyncio.Future()
        return True

    manager = _manager(executor, persist, "contrarian")
    task = asyncio.create_task(executor.apply(
        [_Sig("long")], ref_px=30000, bar_index=0, ts=1))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls["orders"] == []
    assert manager.pending is None
    assert manager.quota.reserved == 0
    assert snapshots[-1]["pendingOpenAttempt"] is None
    assert executor.last_open_receipt.reason == "canceled_before_send"


@pytest.mark.parametrize("inst_id,price,ct_val", [
    ("BTC-USDT-SWAP", 60000, .01), ("ETH-USDT-SWAP", 3000, .1),
    ("SOL-USDT-SWAP", 150, 1), ("DOGE-USDT-SWAP", .1, 100),
    ("SATS-USDT-SWAP", .00000001, 100000000),
])
async def test_every_eligible_instrument_gets_its_own_accepted_receipt(
        inst_id, price, ct_val):
    executor, calls = _make_executor(ct_val=ct_val)
    executor.inst_id = inst_id

    async def persist(snapshot):
        assert snapshot["pendingOpenAttempt"]["instId"] == inst_id
        return True

    manager = _manager(executor, persist)

    async def post(order):
        calls["orders"].append(order)
        order.submission_trace.phase = "sent"
        order.submission_trace.ack = "accepted"
        order.submission_trace.ord_id = inst_id
        return {"ordId": inst_id, "clOrdId": order.client_order_id}

    async def lookup(identity):
        order = calls["orders"][-1]
        return {"instId": inst_id, "clOrdId": identity, "ordId": inst_id,
                "side": "buy", "sz": order.sz, "accFillSz": order.sz,
                "state": "filled", "avgPx": price}

    executor._place_order, executor._get_order = post, lookup
    await executor.apply([_Sig("long")], ref_px=price, bar_index=0, ts=1)
    assert len(calls["orders"]) == 1
    assert executor.last_open_receipt.status == "accepted"
    assert executor.last_open_receipt.inst_id == inst_id
    assert executor.entry_price == price
    assert manager.quota.used == 0


async def test_adopted_live_order_restores_identity_and_queries_original_id():
    executor, _ = _make_executor()
    snapshots = []

    async def persist(snapshot):
        snapshots.append(snapshot)
        return True

    manager = _manager(executor, persist, "contrarian")
    order = order_builder.OrderInput(
        executor.inst_id, "long", "limit", "cross", 29900, 10, 3,
        31000, 29000, "fixed", order_builder.SubmissionTrace())
    assert await manager.prepare(order)
    await manager.observe(order_builder.SubmissionTrace("sent", "accepted", "okx1"))

    async def lookup(identity):
        return {"instId": executor.inst_id, "side": "buy", "sz": 10,
                "clOrdId": identity, "ordId": "okx1", "state": "live",
                "avgPx": None, "accFillSz": 0}

    executor._get_order = lookup
    assert await manager.resolve_pending(executor, 1) == []
    assert await manager.complete_adoption(executor)
    cold, calls = _make_executor()
    restored = type(manager).restore(
        snapshots[-1], account_id=1, run_id="run", inst_id=cold.inst_id,
        max_per_session=2, persist_callback=persist)
    cold.open_attempts = restored
    cold.restore_trades([])
    assert restored.restore_resting(cold)
    assert cold.projected == "flat"
    assert cold.resting.cl_ord_id == "fixed"
    assert restored.has_pending
    await cold.apply([_Sig("long")], ref_px=30000, bar_index=1, ts=2)
    assert calls["orders"] == []
    cold._get_order = lookup
    await restored.resolve_pending(cold, 3)
    assert restored.quota.used == 1


async def test_market_adoption_uses_frozen_protection_prices():
    executor, _ = _make_executor()

    async def persist(snapshot):
        return True

    manager = _manager(executor, persist)
    assert await manager.prepare(order_builder.OrderInput(
        executor.inst_id, "long", "market", "cross", None, 10, 3,
        32000, 29000, "fixed", order_builder.SubmissionTrace()))

    async def lookup(identity):
        return {"instId": executor.inst_id, "side": "buy", "sz": 10,
                "clOrdId": identity, "ordId": "okx1", "state": "filled",
                "avgPx": 30100, "accFillSz": 10}

    executor._get_order = lookup
    events = await manager.resolve_pending(executor, 1)
    trade = events[0]["data"]
    assert trade["takeProfitPrice"] == 32000
    assert trade["stopLossPrice"] == 29000


@pytest.mark.parametrize("raw", [{}, {
    "fee": "NaN", "fundingFee": "", "liqPenalty": "Infinity",
    "pnl": None, "realizedPnl": "bad", "settledPnl": "",
}])
def test_position_history_missing_amounts_remain_unknown(raw):
    normalized = trading._normalize_position_history(raw)
    for key in ("fee", "fundingFee", "liqPenalty", "pnl",
                "realizedPnl", "settledPnl", "identityGapUsd"):
        assert normalized[key] is None


@pytest.mark.parametrize("changes", [
    {"state": "filled", "accFillSz": 0},
    {"state": "filled", "accFillSz": 4},
    {"state": "filled", "accFillSz": None},
    {"state": "mystery", "accFillSz": 10},
    {"instId": "ETH-USDT-SWAP"}, {"side": "sell"},
    {"accountId": 2}, {"ordId": "foreign"}, {"sz": 20},
])
async def test_uncertain_or_mismatched_order_facts_never_complete_adoption(changes):
    executor, _ = _make_executor()

    async def persist(snapshot):
        return True

    manager = _manager(executor, persist, "contrarian")
    assert await manager.prepare(order_builder.OrderInput(
        executor.inst_id, "long", "market", "cross", None, 10, 3,
        None, None, "fixed", order_builder.SubmissionTrace()))
    await manager.observe(order_builder.SubmissionTrace("sent", "accepted", "okx1"))

    async def lookup(identity):
        return {"instId": executor.inst_id, "side": "buy", "sz": 10,
                "clOrdId": identity, "ordId": "okx1", "state": "filled",
                "avgPx": 100, "accFillSz": 10} | changes

    executor._get_order = lookup
    assert await manager.resolve_pending(executor, 1) == []
    assert not await manager.complete_adoption(executor)
    assert manager.pending is not None
    assert manager.quota.used == 1


@pytest.mark.parametrize("raw", ["unreadable", {
    "schemaVersion": 2, "attemptId": "a", "accountId": 1,
    "runId": "run", "instId": "BTC-USDT-SWAP", "round": 1,
    "runtimeEpochId": "e", "lane": "consensus", "order": None,
    "clOrdId": "fixed",
}])
def test_malformed_pending_remains_blocking(raw):
    from app.ai_auto.open_attempts import OpenAttemptManager
    restored = OpenAttemptManager.restore(
        {"pendingOpenAttempt": raw}, account_id=1, run_id="run",
        inst_id="BTC-USDT-SWAP", max_per_session=2)
    assert restored.has_pending
    assert restored.pending is None


async def test_pending_roundtrip_keeps_unknown_json_keys():
    executor, _ = _make_executor()

    async def persist(snapshot):
        return True

    manager = _manager(executor, persist)
    assert await manager.prepare(order_builder.OrderInput(
        executor.inst_id, "long", "market", "cross", None, 10, 3,
        None, None, "fixed", order_builder.SubmissionTrace()))
    snapshot = manager.runtime_payload()
    snapshot["futureRuntime"] = {"enabled": True}
    snapshot["pendingOpenAttempt"]["futurePending"] = "保留"
    restored = type(manager).restore(
        snapshot, account_id=1, run_id="run", inst_id=executor.inst_id,
        max_per_session=2, persist_callback=persist)
    roundtrip = restored.runtime_payload()
    assert roundtrip["futureRuntime"] == {"enabled": True}
    assert roundtrip["pendingOpenAttempt"]["futurePending"] == "保留"


async def test_cancel_ack_with_unseen_fill_does_not_reprice_into_double_open():
    from tests.test_live_executor_limit import _make_executor as make_limit
    from app.ai_auto.order_plan import OrderPlan
    from app.live_auto.resting import build_resting
    executor, calls = make_limit()

    async def persist(snapshot):
        return True

    _manager(executor, persist)
    executor.resting = build_resting(
        cl_ord_id="fixed", ord_id="okx1", intent="open", side="long",
        px=29900, sz=10, strength=1, reason="测试", now_ts=1, ttl_s=100)

    async def lookup(identity):
        return {"instId": executor.inst_id, "side": "buy", "sz": 10,
                "clOrdId": identity, "ordId": "okx1", "state": "canceled",
                "avgPx": 29900, "accFillSz": 4}

    executor._get_order = lookup
    await executor.apply([_Sig("long")], ref_px=30000, bar_index=1, ts=2,
                         order_plan=OrderPlan(ord_type="limit", px=29800))
    assert calls["orders"] == []
    assert executor.projected == "long" and executor.entry_qty_sz == 4
    assert executor.last_open_receipt.reason == "resting_filled"


async def test_live_order_keeps_attempt_and_estimate_after_pending_is_transferred():
    executor, _ = _make_executor()

    async def persist(snapshot):
        return True

    manager = _manager(executor, persist)
    manager.set_context(round=1, runtime_epoch_id="original",
                        estimate={"factsHash": "frozen"})
    assert await manager.prepare(order_builder.OrderInput(
        executor.inst_id, "long", "limit", "cross", 29900, 10, 3,
        32000, 29000, "fixed", order_builder.SubmissionTrace()))
    attempt_id = manager.pending.attempt_id

    async def lookup(identity):
        return {"instId": executor.inst_id, "side": "buy", "sz": 10,
                "clOrdId": identity, "ordId": "okx1", "state": "live",
                "avgPx": None, "accFillSz": 0}

    executor._get_order = lookup
    await manager.resolve_pending(executor, 1)
    assert await manager.complete_adoption(executor)
    manager.set_context(round=2, runtime_epoch_id="new",
                        estimate={"factsHash": "different"})
    events = await executor._advance_resting(executor.resting, {
        "instId": executor.inst_id, "side": "buy", "sz": 10,
        "clOrdId": "fixed", "ordId": "okx1",
        "state": "partially_filled", "avgPx": 30000, "accFillSz": 4,
    }, 2)
    trade = events[0]["data"]
    assert trade["stopLossPrice"] == 29000
    assert trade["explain"]["meta"]["attemptId"] == attempt_id
    assert trade["explain"]["meta"]["frozenEstimate"]["factsHash"] == "frozen"


@pytest.mark.parametrize("changes", [
    {"instId": "foreign"}, {"accFillSz": None}, {"avgPx": None},
    {"avgPx": float("nan")}, {"avgPx": float("inf")},
])
async def test_managed_resting_poll_keeps_uncertain_facts_blocking(changes):
    from app.live_auto.resting import build_resting
    executor, _ = _make_executor()

    async def persist(snapshot):
        return True

    _manager(executor, persist)
    order = build_resting(
        cl_ord_id="fixed", ord_id="okx1", intent="open", side="long",
        px=29900, sz=10, strength=1, reason="测试", now_ts=1, ttl_s=100)
    executor.resting = order
    events = await executor._advance_resting(order, {
        "instId": executor.inst_id, "side": "buy", "sz": 10,
        "clOrdId": "fixed", "ordId": "okx1", "state": "canceled",
        "avgPx": 29900, "accFillSz": 4,
    } | changes, 2)
    assert not any(event["type"] == "trade" for event in events)
    assert executor.resting == order
    assert executor.projected == "flat"


def test_order_normalization_does_not_invent_zero_accumulated_fill():
    assert trading._normalize_order({})["accFillSz"] is None
    assert trading._normalize_order({"accFillSz": "0"})["accFillSz"] == 0


async def test_resolver_clears_last_receipt_when_no_pending_remains():
    executor, _ = _make_executor()

    async def persist(snapshot):
        return True

    manager = _manager(executor, persist)
    assert await manager.prepare(order_builder.OrderInput(
        executor.inst_id, "long", "market", "cross", None, 10, 3,
        None, None, "fixed", order_builder.SubmissionTrace()))
    await manager.observe(order_builder.SubmissionTrace("sent", "rejected"))
    assert manager.last_receipt.status == "rejected"
    assert await manager.resolve_pending(executor, 2) == []
    assert manager.last_receipt is None


@pytest.mark.parametrize("kind", ["cleared", "same", "foreign", "failed"])
async def test_execution_sync_persists_resting_progress_without_losing_identity(kind):
    from app.live_auto.resting import build_resting
    from dataclasses import asdict
    executor, _ = _make_executor()
    writes = []

    async def persist(snapshot):
        writes.append(snapshot)
        return kind != "failed"

    manager = _manager(executor, persist)
    original = build_resting(
        cl_ord_id="fixed", ord_id="okx1", intent="open", side="long",
        px=29900, sz=10, strength=1, reason="测试", now_ts=1, ttl_s=100)
    saved = {"attemptId": "original", "clOrdId": "fixed",
             "resting": asdict(original), "position": None}
    manager._extras["adoptedOrder"] = saved
    executor.resting = (
        replace(original, cl_ord_id="foreign") if kind == "foreign"
        else replace(original, adopted_sz=4) if kind == "same" else None)
    result = await manager.sync_execution(executor)
    if kind in {"foreign", "failed"}:
        assert not result
        assert manager.runtime_payload()["adoptedOrder"] == saved
        assert len(writes) == (1 if kind == "failed" else 0)
    else:
        assert result
        assert len(writes) == 1
        actual = writes[0]["adoptedOrder"]
        assert actual["attemptId"] == "original"
        assert actual["resting"] == (
            asdict(executor.resting) if executor.resting else None)
        assert await manager.sync_execution(executor)
        assert len(writes) == 1
