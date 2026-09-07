"""手续费比较证据：同次冻结、同笔配对、旧订单恢复与亏损事件保真。"""
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ai_auto.attribution import AttributionLedger
from app.ai_auto.cost_runtime import CostRuntime
from app.ai_auto.experience.events import EventIdentity, OpenContext, _outcome_wire, build_event
from app.ai_auto.open_attempts import PendingOpenAttempt
from app.ai_auto.trip_ledger import (
    RoundEstimate, SettleContext, build_round_estimate, open_snapshot, settle,
    trip_meta_wire,
)
from app.config import Settings
from app.db import ai_trade_state_repositories as state_repo
from app.db.models import Base


def _outcome(*, fee_open=4.0, fee_close=6.0, model=3):
    return SimpleNamespace(edge_bps=40.0, cost_bps=16.0, wire={
        "edgeSource": "atr", "costConfidence": "ok", "cost": {
            "feeOpenBps": fee_open, "feeCloseBps": fee_close,
            "totalBps": 16.0, "modelVersion": model}})


def _estimate(**kwargs):
    return build_round_estimate(_outcome(**kwargs),
        SimpleNamespace(confidence=.8, ord_type="limit"), "candle")


def _trade(identifier, side, *, qty=10.0, fee=.5, pnl=0.0, **meta):
    return {"id": identifier, "ts": identifier * 100, "price": 100.0,
        "qty": qty, "side": side, "fee": fee, "pnl": pnl,
        "explain": {"meta": {"feeBasis": "measured", "feeKnown": True,
            "feeCurrency": "USDT", "wallTs": identifier * 100, **meta}}}


def _settled(*, estimate=None, open_fee=.5, close_fee=.5, **meta):
    opened = open_snapshot(_trade(1, "long", fee=open_fee), seq=1,
        now_wall=100, atr_px=None, ct_val=1, estimate=estimate)
    return settle(opened, _trade(2, "close", fee=close_fee, **meta),
        SettleContext(net_mode=False, ct_val=1))


def test_total_cost_is_not_subtracted_from_measured_fees():
    estimate = _estimate()
    assert getattr(estimate, "est_fee_bps", None) == 10.0
    assert estimate.model_version == 3
    trip = _settled(estimate=estimate)
    assert trip.est_cost_bps == 16.0
    assert trip.realized_cost_bps == 10.0
    assert trip.fee_error_bps == 0.0
    assert trip.cost_error_bps is None
    wire = trip_meta_wire(trip)
    assert wire["estFeeBps"] == wire["realizedFeeBps"] == 10.0
    assert wire["feeErrorBps"] == 0.0
    assert wire["comparisonVersion"] == 1
    assert wire["costComparisonBasis"] == "fees"
    assert wire["costErrorBps"] is None


@pytest.mark.parametrize("fee", [0.0, -.25])
def test_zero_and_rebate_fees_keep_sign_and_pair(fee):
    trip = _settled(estimate=_estimate(fee_open=fee * 10,
        fee_close=fee * 10), open_fee=fee, close_fee=fee)
    assert getattr(trip, "fee_measurement", None) == "measured"
    assert trip.fee_usd == 2 * fee
    assert trip.net_pnl_usd == -2 * fee
    assert trip.fee_error_bps == 0.0


def test_legacy_total_estimate_preserves_loss_without_comparison():
    old = RoundEstimate(40, 16, "atr", "ok", .8, "candle")
    trip = _settled(estimate=old)
    assert trip.net_pnl_usd == -1.0
    assert trip.cost_error_bps is None
    assert getattr(trip, "fee_error_bps", None) is None
    outcome = _outcome_wire(trip)
    assert outcome["netPnlUsd"] == -1.0
    assert "feeErrorBps" not in outcome
    assert "costErrorBps" not in outcome


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), "bad", None])
def test_bad_fee_decomposition_never_produces_comparison(bad):
    estimate = _estimate(fee_open=bad)
    assert getattr(estimate, "est_fee_bps", None) is None
    assert getattr(estimate, "comparison_version", None) is None
    assert _settled(estimate=estimate).net_pnl_usd == -1.0


@pytest.mark.parametrize("basis", ["estimated", "unknown"])
def test_estimated_or_unknown_fees_never_pair(basis):
    trip = _settled(estimate=_estimate(), feeBasis=basis)
    if basis == "unknown":
        assert trip is None
    else:
        assert getattr(trip, "fee_measurement", None) == "estimated"
        assert trip.fee_error_bps is None


def test_legacy_and_future_frozen_estimates_decode_without_current_defaults():
    ledger = AttributionLedger(ct_val=1)
    legacy = {"est_edge_bps": 40, "est_cost_bps": 16, "edge_source": "atr",
        "cost_confidence": "ok", "decision_confidence": .8, "wake_kind": "candle"}
    old = ledger._take_estimate(_trade(1, "long", frozenEstimate=legacy))
    assert old.model_version == 2
    assert getattr(old, "comparison_version", "missing") is None
    future = {**legacy, "model_version": 99, "comparison_version": 99,
        "est_fee_bps": 10, "factsHash": "original"}
    decoded = ledger._take_estimate(_trade(1, "long", frozenEstimate=future))
    assert decoded.model_version == decoded.comparison_version == 99
    assert decoded.facts_hash == "original"
    assert _settled(estimate=decoded).fee_error_bps is None


@pytest.mark.parametrize("key", ["est_fee_bps", "est_cost_bps", "est_edge_bps",
    "decision_confidence", "as_of_ts", "comparison_version", "model_version"])
@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), {}])
def test_bad_optional_frozen_fields_do_not_prevent_settling_loss(key, bad):
    raw = {**asdict(_estimate()), key: bad}
    ledger = AttributionLedger(ct_val=1)
    ledger.note_open(_trade(1, "long", frozenEstimate=raw), seq=1)
    ledger.settle_close(_trade(2, "close"))
    trip = ledger.last_settled()
    assert trip is not None and trip.net_pnl_usd == -1.0
    assert getattr(ledger._take_estimate(_trade(3, "long",
        frozenEstimate=raw)), key) is None


def test_new_experience_outcome_keeps_fee_basis_and_loss():
    outcome = _outcome_wire(_settled(estimate=_estimate()))
    assert outcome["netPnlUsd"] == -1.0
    assert outcome.get("feeErrorBps") == 0.0
    assert outcome["comparisonVersion"] == 1
    assert outcome["costComparisonBasis"] == "fees"
    assert "costErrorBps" not in outcome


def test_missing_fee_estimate_does_not_drop_complete_loss_event():
    opened = OpenContext(1, "long", {"side": "long"}, 100)
    trip = _settled()
    event = build_event(opened, trip, {"_costBasis": "net", "wallTs": 200},
        EventIdentity("group", 1, "BTC-USDT-SWAP", "15m"))
    assert event is not None
    assert event.row()["outcome"]["netPnlUsd"] == -1.0
    assert event.row()["net_bps"] == -10.0
    assert "feeErrorBps" not in event.row()["outcome"]


def test_fee_error_uses_unrounded_realized_fee_bps():
    trip = _settled(estimate=_estimate(fee_open=5.00004, fee_close=5.00004),
        open_fee=.500002, close_fee=.500002)
    assert trip.realized_fee_bps == 10.0
    assert trip.fee_error_bps == 0.0


def test_current_round_snapshot_does_not_relabel_outcome_model():
    runtime = CostRuntime("BTC-USDT-SWAP", Settings(), None)
    runtime.begin_round_cost({"asOfTs": 1000})
    runtime.note_estimate(_outcome(model=99),
        SimpleNamespace(confidence=.8, ord_type="limit"), "candle")
    assert runtime.pending_estimate()["model_version"] == 99
    assert runtime.pending_estimate()["as_of_ts"] == 1000


def test_present_but_corrupt_order_estimate_never_uses_current_round():
    ledger = AttributionLedger(ct_val=1)
    ledger.note_estimate(_estimate())
    ledger.note_open(_trade(1, "long", frozenEstimate="corrupt"), seq=1)
    ledger.settle_close(_trade(2, "close"))
    assert ledger.last_settled().est_fee_bps is None
    assert ledger.last_settled().fee_error_bps is None


def test_rolling_comparison_coverage_uses_all_window_trips():
    ledger = AttributionLedger(capacity=2, ct_val=1)
    for index, estimate in enumerate((_estimate(fee_open=0, fee_close=0),
                                      _estimate(), None)):
        ledger.note_estimate(estimate)
        ledger.note_open(_trade(index * 2 + 1, "long"), seq=index)
        ledger.settle_close(_trade(index * 2 + 2, "close"))
    stats = ledger.realized_stats()
    assert stats["trips"] == 2
    assert stats.get("feeComparisonTrips") == 1
    assert stats["feeComparisonCoverage"] == .5
    assert stats["feeErrorMedianBps"] == 0.0
    assert stats["costErrorMedianBps"] is None


async def test_pending_sqlite_reload_partial_fills_keep_original_estimate(tmp_path):
    runtime = CostRuntime("BTC-USDT-SWAP", Settings(), None)
    runtime.note_estimate(_outcome(), SimpleNamespace(confidence=.8,
        ord_type="limit"), "candle")
    pending = PendingOpenAttempt("attempt", 7, "1", "BTC-USDT-SWAP", 1,
        "epoch", "consensus", {"inst_id": "BTC-USDT-SWAP",
            "direction": "long", "ord_type": "limit", "sz": 10}, "fixed",
        estimate=runtime.pending_estimate())
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fees.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            await state_repo.upsert_group_state(db, state_repo.GroupStateWrite(
                "fees", "running", account_id=7, members=[{
                    "instId": "BTC-USDT-SWAP", "runId": 1,
                    "runtimeIntegrity": {"pendingOpenAttempt": pending.to_dict()}}]))
        async with factory() as db:
            group = await state_repo.load_group_state(db, "fees")
            wire = group["members"][0]["runtimeIntegrity"]["pendingOpenAttempt"]
    finally:
        await engine.dispose()
    restored = PendingOpenAttempt.from_dict(wire)
    assert restored.estimate.get("est_fee_bps") == 10.0
    assert restored.estimate["model_version"] == 3
    assert restored.estimate["comparison_version"] == 1
    ledger = AttributionLedger(ct_val=1)
    ledger.note_estimate(_estimate(fee_open=10, fee_close=20))
    first = _trade(1, "long", qty=4, fee=.2,
        frozenEstimate=restored.estimate)
    ledger.note_open(first, seq=1)
    ledger.end_round()
    ledger.restore_accounting([first], {"costBasis": "gross", "ctVal": 1})
    ledger.note_open(_trade(2, "long", qty=6, fee=.3), seq=0)
    ledger.settle_close(_trade(3, "close", qty=4, fee=.2))
    assert ledger.last_settled() is None
    final = _trade(4, "close", qty=6, fee=.3)
    ledger.settle_close(final)
    ledger.settle_close(final)
    assert len(ledger.settled_trips()) == 1
    assert ledger.last_settled().est_fee_bps == 10.0
    assert ledger.last_settled().fee_error_bps == 0.0


def test_runtime_cost_wire_uses_frozen_model_version():
    runtime = CostRuntime("BTC-USDT-SWAP", Settings(), None)
    snapshot = runtime.begin_round_cost({"asOfTs": 1000})
    runtime._round_cost = replace(snapshot, model_version=99)
    assert runtime.round_cost_wire()["modelVersion"] == 99


def test_completed_replay_restores_comparison_window_once():
    raw = asdict(_estimate())
    trades = [_trade(1, "long", frozenEstimate=raw), _trade(2, "close")]
    ledger = AttributionLedger(ct_val=1)
    for _ in range(2):
        ledger.restore_accounting(trades, {"costBasis": "gross", "ctVal": 1})
        stats = ledger.realized_stats()
        assert stats is not None and stats["feeComparisonTrips"] == 1
        assert stats["feeErrorMedianBps"] == 0.0
        assert ledger.last_settled() is None
