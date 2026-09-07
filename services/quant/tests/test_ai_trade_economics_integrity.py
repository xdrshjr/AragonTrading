"""费用来源、双腿恒等式与完整 run 重放的回归证据。"""
from __future__ import annotations

import copy

import pytest

from app.ai_auto.attribution import AttributionLedger
from app.ai_auto.cost_model import CostQuery, estimate_round_trip_cost
from app.ai_auto.cost_model import resolve_cost_params
from app.ai_auto.cost_runtime import CostRuntime
from app.config import Settings
from app.live_auto.fee_ledger import FeeLedger, resolve_fee_params


def leg(number, side, qty, price, fee, pnl=0.0, after=None, **meta):
    """提供明确来源与稳定身份的增量成交夹具。"""
    return {"id": number, "runId": 7, "side": side, "qty": qty,
            "price": price, "fee": fee, "pnl": pnl,
            "positionAfter": after, "ts": 1000 - number,
            "explain": {"meta": {"accountingEventId": f"event-{number}",
                "feeBasis": "measured", "feeKnown": fee is not None,
                "feeCurrency": "USDT", "accountingVersion": 2,
                "wallTs": 1000 + number, **meta}}}


def accumulator(basis="net"):
    from app.ai_auto.accounting_replay import TradeAccountingAccumulator
    return TradeAccountingAccumulator(
        {"runId": 7, "costBasis": basis, "ctVal": 1.0})


@pytest.mark.parametrize("basis", ["gross", "net"])
@pytest.mark.parametrize("official_fee", [-.1, 0.0, .2])
def test_validated_settlement_signed_residual_survives_complete_replay(
        basis, official_fee):
    from app.live_auto.settlement import SettlementQuery
    from app.live_auto.settlement import resolve_settlement_increment
    from app.live_auto.settlement import validate_settlement
    from app.live_auto.settlement_identity import IdentityConfig
    from app.live_auto.settlement_identity import SettlementIdentityTracker
    tracker = SettlementIdentityTracker(IdentityConfig(
        1, 7, "SOL-USDT-SWAP", {"marginMode": "cross", "tickSz": .01}))
    opened = leg(1, "long", 10, 100, .5, after=10,
                 feeBasis="estimated")
    tracker.record_trade(opened)
    tracker.observe_position(dict(instId="SOL-USDT-SWAP", posId="p1",
        posSide="net", pos=10, avgPx=100, marginMode="cross",
        exchangeCTimeMs="1000000", exchangeUTimeMs="1100000",
        observedAtTs=1100))
    current = tracker.context()
    query = SettlementQuery("SOL-USDT-SWAP", "long", 1001, 1200,
                            context=current)
    normalized = dict(instId="SOL-USDT-SWAP", posId="p1",
        instType="SWAP", currency="USDT", closed=True, type="2",
        marginMode="cross", exchangeCTimeMs="1000000",
        exchangeUTimeMs="1200000", direction="long", closeTotalPos=10,
        openAvgPx=100, closeAvgPx=101, pnl=10, fee=official_fee,
        fundingFee=0, liqPenalty=0, settledPnl=0,
        realizedPnl=10 - official_fee)
    original = copy.deepcopy(opened)
    fact = validate_settlement([normalized], query, {}, 1201).fact
    increment = resolve_settlement_increment(fact, current, basis).increment
    closed = leg(2, "close", increment.qty, increment.price, increment.fee,
        increment.pnl, after=0, feeBasis=increment.fee_basis,
        accountingEventId=increment.accounting_event_id,
        feeAdjustment=increment.fee_adjustment)
    acc = accumulator(basis)
    acc.restore([opened, closed, copy.deepcopy(closed)])
    snapshot = acc.snapshot()
    assert snapshot["roundTripCount"] == 1
    assert snapshot["grossPnlUsd"] == pytest.approx(10, abs=1e-9)
    assert snapshot["feeUsd"] == pytest.approx(official_fee, abs=1e-9)
    assert snapshot["tradingNetUsd"] == pytest.approx(
        10 - official_fee, abs=1e-9)
    assert closed["explain"]["meta"]["feeBasis"] == "estimated"
    assert opened == original


@pytest.mark.parametrize("basis,pnl", [("net", 9.0), ("gross", 10.0)])
def test_signed_both_leg_fees_have_one_economic_identity(basis, pnl):
    from app.ai_auto.trade_economics import TripPnlInputs
    from app.ai_auto.trade_economics import resolve_trip_economics
    result = resolve_trip_economics(TripPnlInputs(basis, pnl, 1.2, -0.2))
    assert result.gross_pnl_usd == pytest.approx(10.0)
    assert result.fee_usd == pytest.approx(1.0)
    assert result.trading_net_usd == pytest.approx(9.0)
    assert result.coverage == "complete"


def test_unknown_fee_keeps_independently_reliable_net():
    from app.ai_auto.trade_economics import TripPnlInputs
    from app.ai_auto.trade_economics import resolve_trip_economics
    result = resolve_trip_economics(TripPnlInputs("net", 4.0, None, 0.0))
    assert result.trading_net_usd == 4.0
    assert result.gross_pnl_usd is None and result.fee_usd is None
    bad = resolve_trip_economics(TripPnlInputs("net", float("nan"), 0, 0))
    assert bad.trading_net_usd is None


def test_projected_net_with_unknown_entry_fee_is_not_certified_or_sampled():
    """执行估算保留原始值，认证统计不能拿它补未知开仓费。"""
    rows = [leg(1, "long", 1, 100, None, after=1),
            leg(2, "close", 1, 110, .2, 9.8, after=0,
                pnlBasis="estimated", entryFeeBasis="unknown")]
    original = copy.deepcopy(rows)
    acc = accumulator("net")
    snap = acc.restore(rows)
    assert snap["tradingNetUsd"] is None
    assert snap["winRate"] is None and snap["avgPnlUsd"] is None
    assert snap["metricCoverage"]["winRate"]["sampleCount"] == 0
    assert snap["unknownTripCount"] == 1 and snap["roundTripCount"] == 1
    assert acc.completed_trips[0]["recordedPnl"] == 9.8
    assert acc.remaining_qty == 0
    assert rows == original
    gross = accumulator("gross").restore(rows)
    assert gross["grossPnlUsd"] == 9.8  # 毛口径不依赖未知开仓费
    assert gross["tradingNetUsd"] is None


def test_partial_fills_and_closes_allocate_fees_and_complete_once():
    acc = accumulator("gross")
    rows = [leg(1, "long", 4, 100, .4, after=4),
            leg(2, "long", 6, 110, .6, after=10),
            leg(3, "close", 4, 115, .2, 36, after=6),
            leg(4, "close", 6, 120, .3, 84, after=0)]
    original = copy.deepcopy(rows)
    for row in rows[:3]:
        acc.record_trade(row)
    assert acc.snapshot()["roundTripCount"] == 0
    assert acc.remaining_open_fee_usd == pytest.approx(.6)
    assert acc.remaining_qty == 6
    acc.record_trade(rows[3])
    acc.record_trade(copy.deepcopy(rows[3]))
    snap = acc.snapshot()
    assert snap["roundTripCount"] == 1
    assert snap["grossPnlUsd"] == 120
    assert snap["feeUsd"] == pytest.approx(1.5)
    assert snap["tradingNetUsd"] == pytest.approx(118.5)
    assert acc.completed_trips[0]["entryPrice"] == 106
    assert acc.completed_trips[0]["exitPrice"] == 118
    assert acc.completed_trips[0]["notionalUsd"] == 1060
    assert rows == original


def test_inconsistent_quantity_and_orphan_close_remain_partial():
    acc = accumulator()
    acc.record_trade(leg(1, "close", 1, 100, .1, 4, after=0))
    acc.record_trade(leg(2, "long", 2, 100, .2, after=2))
    acc.record_trade(leg(3, "close", 1, 101, .1, .8, after=0))
    snap = acc.snapshot()
    assert snap["roundTripCount"] == 0
    assert snap["coverage"] == "partial"
    assert snap["unknownTripCount"] >= 1
    assert acc.remaining_qty == 1


def test_replay_uses_ids_and_preserves_more_than_ring_capacity():
    acc = accumulator()
    rows = []
    for index in range(205):
        rows.extend([leg(index * 2 + 1, "long", 1, 100, .2, after=1),
                     leg(index * 2 + 2, "close", 1, 102, -.1,
                         1.9, after=0)])
    acc.restore(list(reversed(rows)))
    first = acc.snapshot()
    acc.restore(rows)
    second = acc.snapshot()
    for key in ("roundTripCount", "grossPnlUsd", "feeUsd",
                "sessionFeeUsd", "tradingNetUsd", "winRate"):
        assert first[key] == second[key]
    assert second["roundTripCount"] == 205
    assert second["feeUsd"] == pytest.approx(20.5)
    assert second["grossPnlUsd"] == pytest.approx(410)
    assert second["tradingNetUsd"] == pytest.approx(389.5)


def test_zero_fee_provenance_survives_and_legacy_zero_is_unknown():
    acc = accumulator()
    rows = [leg(1, "long", 1, 100, 0, after=1),
            leg(2, "close", 1, 100, 0, 0, after=0)]
    acc.restore(rows)
    assert acc.snapshot()["completeFeeTripCount"] == 1
    assert acc.snapshot()["winRate"] == 0
    for row in rows:
        row["explain"] = {}
    acc.restore(rows)
    assert acc.snapshot()["coverage"] == "legacy_partial"
    assert acc.snapshot()["sessionFeeUsd"] is None


def test_attribution_uses_both_fees_and_does_not_reuse_last_settlement():
    ledger = AttributionLedger(net_mode=True, ct_val=1)
    opened = leg(1, "long", 1, 100, 1.2, after=1)
    closed = leg(2, "close", 1, 110, -.2, 9, after=0)
    ledger.note_open(opened, seq=1, now_wall=1001)
    ledger.record_close(closed)
    ledger.settle_close(closed)
    assert ledger.last_settled().gross_pnl_usd == 10
    assert ledger.lifetime_cost()["feeUsd"] == pytest.approx(1)
    ledger.record_close(closed)
    assert ledger.lifetime_cost()["roundTrips"] == 1
    assert ledger.last_settled() is None
    ledger.record_close(leg(3, "close", 1, 110, .1, None, after=0))
    assert ledger.last_settled() is None


def test_live_settlement_uses_top_level_wall_time_before_meta_is_persisted():
    ledger = AttributionLedger(net_mode=True, ct_val=1)
    opened = leg(1, "long", 1, 100, .2, after=1)
    closed = leg(2, "close", 1, 101, .1, .7, after=0)
    closed["ts"] = 900
    closed["wallTs"] = 1100
    closed["explain"]["meta"].pop("wallTs")
    ledger.note_open(opened, seq=1, now_wall=1001)
    ledger.record_close(closed)
    wire = ledger.settle_close(closed)
    assert wire["holdSeconds"] == 99
    assert ledger.last_settled().hold_seconds == 99


def test_fee_ledger_limit_estimate_and_explicit_zero_rebate_provenance():
    settings = Settings(ai_trade_fee_accounting_enabled=True)
    fees = FeeLedger(ct_val=1, params=resolve_fee_params(settings))
    assert fees.leg_fee_bps("limit") == settings.ai_trade_taker_fee_bps
    base = dict(cl_ord_id=None, side="long", sz=1, px=100,
                ord_type="limit", expected_px=100)
    assert fees.on_trade(**base)["feeBasis"] == "estimated"
    for fee in (0, -.1):
        row = fees.on_trade(**base, override_fee_usd=fee)
        assert row["fee"] == fee
        assert row["feeBasis"] == "measured" and row["feeKnown"]
    row = fees.on_trade(**base, override_fee_usd=float("nan"))
    assert row["feeBasis"] == "unknown" and not row["feeKnown"]


def test_frozen_round_fee_inputs_do_not_change_during_model_wait():
    settings = Settings(ai_trade_funding_enabled=False)
    fees = FeeLedger(ct_val=1, params=resolve_fee_params(settings))
    runtime = CostRuntime("BTC-USDT-SWAP", settings, fees)
    first = runtime.begin_round_cost({"bar_seconds": 900})
    cost = runtime.estimate_preview_cost(
        ord_type="limit", side="long", bar_seconds=900)
    fees.retune(maker_bps=10, taker_bps=20, source="exchange")
    same = runtime.estimate_preview_cost(
        ord_type="limit", side="long", bar_seconds=900)
    assert cost == same
    assert runtime.round_cost_wire()["modelVersion"] == 2
    runtime.begin_round_cost({"bar_seconds": 900})
    changed = runtime.estimate_preview_cost(
        ord_type="limit", side="long", bar_seconds=900)
    assert changed.total_bps > same.total_bps
    assert first.params.taker_fee_bps == settings.ai_trade_taker_fee_bps
    assert runtime.pre_submit_context_changed() is False
    assert changed.fee_source == "exchange"


@pytest.mark.parametrize("changes", [
    {"rate": .002}, {"interval_s": 3600}, {"interval_known": False},
    {"next_funding_ts": 7200},
])
def test_pre_submit_rechecks_changed_funding_terms(monkeypatch, changes):
    from dataclasses import replace
    from app.ai_auto.funding import FundingSnapshot

    monkeypatch.setattr("app.ai_auto.cost_runtime.time.time", lambda: 1000)
    runtime = CostRuntime("BTC-USDT-SWAP", Settings(), None)
    original = FundingSnapshot(.001, 0, 3600, 28800, 999, True)
    runtime.funding._snapshot = original
    runtime.begin_round_cost()
    runtime.funding._snapshot = replace(original, fetched_wall=1000)
    assert runtime.pre_submit_context_changed() is False
    runtime.funding._snapshot = replace(original, **changes)
    assert runtime.pre_submit_context_changed() is True
    assert runtime._round_cost.funding == original


def test_close_accrues_funding_crossing_after_last_monitor_tick(monkeypatch):
    from app.ai_auto.funding import FundingSnapshot

    settings = Settings(ai_trade_funding_enabled=True,
                        ai_trade_funding_ledger_enabled=True)
    fees = FeeLedger(ct_val=1, params=resolve_fee_params(settings))
    runtime = CostRuntime("BTC-USDT-SWAP", settings, fees)
    snapshot = FundingSnapshot(.001, 0, 3600, 3600, 3599, True)
    runtime.funding._snapshot = snapshot
    runtime.note_open(leg(1, "long", 1, 100, .2, after=1),
                      seq=1, now_wall=3500)
    runtime.funding_ledger.accrue(snapshot, now_wall=3599, mark_px=100)
    monkeypatch.setattr("app.ai_auto.cost_runtime.time.time", lambda: 3601)
    closed = leg(2, "close", 1, 100, .1, -.3, after=0)
    closed["wallTs"] = 3601
    runtime.record_close(closed)
    economics = runtime.economics_snapshot()
    assert economics["fundingCostUsd"] == pytest.approx(.1)
    assert economics["netAfterFundingUsd"] == pytest.approx(-.4)
    runtime.record_close(closed)
    assert runtime.economics_snapshot()["fundingCostUsd"] == pytest.approx(.1)


def test_optional_enrichment_failure_does_not_leave_fees_in_next_position(monkeypatch):
    import app.ai_auto.attribution as attribution
    settings = Settings(ai_trade_trip_ledger_enabled=True)
    fees = FeeLedger(ct_val=1, params=resolve_fee_params(settings))
    runtime = CostRuntime("BTC-USDT-SWAP", settings, fees)
    runtime.note_open(leg(1, "long", 1, 100, .2, after=1), seq=1, now_wall=1001)
    close = leg(2, "close", 1, 101, .1, .7, after=0)
    def unavailable(*args, **kwargs):
        raise ValueError("富化不可用")
    monkeypatch.setattr(attribution, "settle", unavailable)
    runtime.record_close(close)
    runtime.apply_trip_meta({}, close)
    assert runtime.economics_snapshot()["tradingNetUsd"] == pytest.approx(.7)
    assert runtime.last_settled_trip() is None
    runtime.note_open(leg(3, "long", 1, 101, .3, after=1), seq=2, now_wall=1003)
    assert runtime.attribution.accounting.remaining_open_fee_usd == .3


def test_runtime_restore_has_full_session_fee_and_unknown_funding():
    settings = Settings()
    fees = FeeLedger(ct_val=1, params=resolve_fee_params(settings))
    runtime = CostRuntime("BTC-USDT-SWAP", settings, fees)
    rows = [leg(1, "long", 1, 100, .2, after=1),
            leg(2, "close", 1, 101, .1, .7, after=0),
            leg(3, "long", 2, 100, .4, after=2)]
    context = {"runId": 7, "costBasis": "net", "ctVal": 1}
    runtime.restore_accounting(rows, context)
    runtime.restore_accounting(rows, context)
    assert fees.session_fee_usd == pytest.approx(.7)
    assert runtime.attribution.accounting.remaining_open_fee_usd == .4
    snap = runtime.economics_snapshot()
    assert snap["tradingNetUsd"] == .7
    assert snap["fundingCostUsd"] is None
    assert snap["netAfterFundingUsd"] is None
    assert runtime.last_settled_trip() is None


@pytest.mark.parametrize("completed", [0, 2])
def test_cold_partial_fill_keeps_existing_epoch_quantity_and_fee(completed):
    runtime = CostRuntime("BTC-USDT-SWAP", Settings(), None)
    rows = []
    for index in range(completed):
        rows.extend([leg(2 * index + 1, "long", 1, 100, .2, after=1),
                     leg(2 * index + 2, "close", 1, 101, .1, .7, after=0)])
    start = 2 * completed + 1
    rows.append(leg(start, "long", 4, 100, .4, after=4))
    runtime.restore_accounting(rows,
        {"runId": 7, "costBasis": "net", "ctVal": 1})
    runtime.note_open(leg(start + 1, "long", 6, 110, .6, after=10),
                      seq=completed, now_wall=1000 + start + 1)
    assert runtime.attribution.accounting.remaining_qty == 10
    assert runtime.remaining_open_fee_usd == pytest.approx(1)
    assert runtime.attribution._open.seq == completed
    closed = leg(start + 2, "close", 10, 120, .2, 138.8, after=0)
    runtime.record_close(closed)
    runtime.apply_trip_meta({}, closed)
    assert runtime.economics_snapshot()["roundTripCount"] == completed + 1
    assert runtime.economics_snapshot()["coverage"] == "complete"


def test_bad_close_pnl_is_not_a_break_even_learning_sample():
    acc = accumulator()
    acc.restore([leg(1, "long", 1, 100, .1, after=1),
                 leg(2, "close", 1, 100, .1, float("nan"), after=0)])
    snap = acc.snapshot()
    assert snap["roundTripCount"] == 1 and snap["unknownTripCount"] == 1
    assert snap["tradingNetUsd"] is None and snap["winRate"] is None


def test_recovered_order_claims_its_frozen_estimate_before_current_round():
    from dataclasses import asdict
    from app.ai_auto.trip_ledger import RoundEstimate
    fees = FeeLedger(ct_val=1, params=resolve_fee_params(Settings()))
    runtime = CostRuntime("BTC-USDT-SWAP", Settings(), fees)
    original = RoundEstimate(40, 13, "atr", "ok", .8, "interval")
    runtime.attribution.note_estimate(original)
    pending = runtime.pending_estimate()
    assert pending["est_cost_bps"] == 13
    runtime.attribution.note_estimate(RoundEstimate(80, 26, "atr", "ok", .9, "interval"))
    opened = leg(1, "long", 1, 100, .2, after=1,
                 frozenEstimate={**asdict(original), "factsHash": "original"})
    runtime.note_open(opened, seq=1, now_wall=1001)
    closed = leg(2, "close", 1, 102, .1, 1.7, after=0)
    runtime.record_close(closed)
    runtime.apply_trip_meta({}, closed)
    assert runtime.last_settled_trip().est_cost_bps == 13


def test_close_from_other_explicit_epoch_cannot_consume_current_fee_pool():
    acc = accumulator()
    acc.record_trade(leg(1, "long", 2, 100, .2, after=2, positionEpoch="one"))
    acc.record_trade(leg(2, "close", 2, 100, .1, -.3,
                         after=0, positionEpoch="other"))
    assert acc.remaining_qty == 2
    assert acc.remaining_open_fee_usd == .2
    assert acc.snapshot()["roundTripCount"] == 0


def test_runtime_replay_preserves_explicit_persisted_epoch_identity():
    fees = FeeLedger(ct_val=1, params=resolve_fee_params(Settings()))
    runtime = CostRuntime("BTC-USDT-SWAP", Settings(), fees)
    rows = [leg(1, "long", 1, 100, .2, after=1, positionEpoch="epoch-original"),
            leg(2, "close", 1, 101, .1, .7, after=0, positionEpoch="epoch-original")]
    restored = runtime.restore_accounting(rows,
        {"runId": 7, "costBasis": "net", "ctVal": 1})
    assert restored["roundTripCount"] == 1
    assert restored["tradingNetUsd"] == .7


def test_optional_trip_ledger_switch_does_not_disable_frozen_order_estimate():
    from types import SimpleNamespace
    settings = Settings(ai_trade_trip_ledger_enabled=False)
    runtime = CostRuntime("BTC-USDT-SWAP", settings, None)
    runtime.begin_round_cost({"asOfTs": 1000})
    runtime.note_estimate(SimpleNamespace(gated=True, edge_bps=30,
        cost_bps=13, edge_source="atr", cost_confidence="ok"),
        SimpleNamespace(action="open_long", confidence=.8), "interval")
    pending = runtime.pending_estimate()
    assert pending is not None
    assert pending["as_of_ts"] == 1000 and pending["model_version"] == 2


def test_open_funding_cap_is_unknown_for_break_even_context():
    settings = Settings(ai_trade_funding_enabled=True, ai_trade_funding_ledger_enabled=True)
    fees = FeeLedger(ct_val=1, params=resolve_fee_params(settings))
    runtime = CostRuntime("BTC-USDT-SWAP", settings, fees)
    runtime.note_open(leg(1, "long", 1, 100, .2, after=1), seq=1, now_wall=1001)
    epoch = runtime.funding_ledger.epoch
    epoch.settled_through_ts = 1100
    epoch.accrued_usd = .3
    epoch.cap_hits = 1
    assert runtime.economics_snapshot()["openFundingAccruedUsd"] is None


@pytest.mark.parametrize("side,expected", [
    ("long", 1001 / (10 * .999)), ("short", 999 / (10 * 1.001)),
])
def test_break_even_only_uses_remaining_fee_funding_and_future_exit(side, expected):
    from app.ai_auto.cost_model import position_break_even_px
    assert position_break_even_px({"side": side, "avgEntry": 100,
        "qtyCoin": 10, "openFeeUsd": .7, "fundingAccruedUsd": .3,
        "closeFeeRate": .001}) == pytest.approx(expected)


def test_plain_limit_cost_is_two_taker_legs():
    settings = Settings()
    cost = estimate_round_trip_cost(
        CostQuery("limit", "long", 900, funding_rate=0),
        resolve_cost_params(settings))
    assert cost.fee_open_bps == settings.ai_trade_taker_fee_bps
    assert cost.crossing_legs == 2


def test_group_pooling_uses_sample_counts_and_does_not_hide_missing_fee():
    from app.ai_auto.trade_economics import merge_economics_snapshots
    left, right = accumulator(), accumulator()
    left.restore([leg(1, "long", 1, 100, .2, after=1),
                  leg(2, "close", 1, 103, .1, 2.7, after=0)])
    right.restore([leg(3, "short", 1, 100, None, after=-1),
                   leg(4, "close", 1, 101, .1, -1.3, after=0),
                   leg(5, "short", 1, 100, .2, after=-1),
                   leg(6, "close", 1, 100, .1, 0, after=0)])
    merged = merge_economics_snapshots([left.snapshot(), right.snapshot()])
    assert merged["roundTripCount"] == 3
    assert merged["winRate"] == pytest.approx(1 / 3)
    assert merged["profitFactor"] == pytest.approx(2.7 / 1.3)
    assert merged["avgPnlUsd"] == pytest.approx(1.4 / 3)
    assert merged["feeUsd"] is None and merged["sessionFeeUsd"] is None
    assert merged["metricCoverage"]["tradingNetUsd"]["sampleCount"] == 3
    assert merged["metricCoverage"]["tradingNetUsd"]["excludedCount"] == 0
    assert merged["metricCoverage"]["feeUsd"]["sampleCount"] == 2
    assert merged["metricCoverage"]["feeUsd"]["excludedCount"] == 1


def test_group_empty_member_is_zero_contribution_without_fabricating_samples():
    from app.ai_auto.trade_economics import merge_economics_snapshots
    complete = accumulator()
    complete.restore([leg(1, "long", 1, 100, .2, after=1),
                      leg(2, "close", 1, 101, .1, .7, after=0)])
    merged = merge_economics_snapshots([complete.snapshot(), accumulator().snapshot()])
    assert merged["tradingNetUsd"] == .7
    assert merged["feeUsd"] == pytest.approx(.3)
    assert merged["roundTripCount"] == 1
    assert merged["profitFactor"] is None
    assert merged["metricCoverage"]["profitFactor"]["reason"] == "no_loss_denominator"


def test_official_settlement_adjustment_is_distinct_and_requires_identity():
    from app.ai_auto.reconcile import ReconcileParams, build_reconciliation
    row = {"instId": "BTC-USDT-SWAP", "posId": "p1", "uTime": 1100,
           "cTime": 1000, "pnl": 10, "fee": .7, "fundingFee": .3,
           "liqPenalty": .4, "settledPnl": .2, "realizedPnl": 8.8,
           "currency": "USDT", "closed": True}
    coverage = {"accountId": "a1", "instId": "BTC-USDT-SWAP",
        "closedTripIds": ["t1"], "positionIds": ["p1"],
        "identityMatched": True, "tripPositionLinks": {"t1": "p1"},
        "windowStartTs": 900, "windowEndTs": 1200, "fetchedAtTs": 1201,
        "pagesComplete": True, "currency": "USDT", "identityHolds": True}
    arguments = dict(local_net_usd=1.0, local_trips=1, rows=[row],
        window_start_ts=900, params=ReconcileParams(.01, .001),
        inst_id="BTC-USDT-SWAP", comparison_coverage=coverage)
    result = build_reconciliation(**arguments)
    assert result["status"] == "drift"
    assert result["exchangeTradingNetUsd"] == pytest.approx(9.3)
    assert result["exchangeNetAfterFundingUsd"] == pytest.approx(9)
    assert result["exchangeRealizedPnlUsd"] == pytest.approx(8.8)
    assert result["comparisonCoverage"]["comparable"]
    from app.ai_auto.trade_economics import merge_economics_snapshots
    merged = merge_economics_snapshots([accumulator().snapshot(result)])
    assert merged["comparisonCoverage"]["comparable"]
    assert merged["exchangeTradingNetUsd"] == pytest.approx(9.3)
    for changed in ({"identityMatched": False}, {"pagesComplete": False}):
        partial = build_reconciliation(**{**arguments,
            "comparison_coverage": {**coverage, **changed}})
        assert partial["exchangeTradingNetUsd"] is None
    unclosed = build_reconciliation(**{**arguments,
        "rows": [{**row, "closed": False}]})
    assert unclosed["exchangeTradingNetUsd"] is None
    truncated = build_reconciliation(**{**arguments, "rows": [row] * 100})
    assert truncated["exchangeTradingNetUsd"] is None
    missing = build_reconciliation(**{**arguments,
        "rows": [{**row, "settledPnl": None}]})
    assert missing["exchangeTradingNetUsd"] is None


@pytest.mark.asyncio
async def test_sqlite_history_and_runtime_replay_share_full_run_economics(tmp_path):
    import json
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy import select
    from app.db.models import Base, Strategy, StrategyRun, Trade
    from app.db.ai_auto_repositories import get_ai_trade_group_detail

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'history.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            strategy = Strategy(name="经济口径测试", template_key="test")
            session.add(strategy)
            await session.flush()
            run = StrategyRun(strategy_id=strategy.id, inst_id="BTC-USDT-SWAP",
                bar="1H", mode="ai-trade", status="stopped", start_ts=1,
                group_id="economics_history", group_started_ts=1,
                config_json=json.dumps({"costBasis": "net", "ctVal": 1}))
            session.add(run)
            await session.flush()
            rows = []
            for index in range(205):
                for row in (leg(index * 2 + 1, "long", 1, 100, .2, after=1),
                            leg(index * 2 + 2, "close", 1, 102, -.1, 1.9, after=0)):
                    row["runId"] = run.id
                    rows.append(row)
                    session.add(Trade(run_id=run.id, ts=row["ts"],
                        side=row["side"], price=row["price"], qty=row["qty"],
                        fee=row["fee"], pnl=row["pnl"],
                        position_after=row["positionAfter"], reason="测试",
                        explain_json=json.dumps(row["explain"])))
            await session.commit()
            before = (await session.execute(select(Trade))).scalars().all()
            detail = await get_ai_trade_group_detail(session, "economics_history")
            actual = detail["summary"]["economics"]
            expected = accumulator().restore(rows,
                {"runId": run.id, "costBasis": "net", "ctVal": 1})
            for key in ("roundTripCount", "feeUsd", "grossPnlUsd",
                        "tradingNetUsd", "winRate", "avgPnlUsd"):
                assert actual[key] == pytest.approx(expected[key])
            await get_ai_trade_group_detail(session, "economics_history")
            after = (await session.execute(select(Trade))).scalars().all()
            assert len(before) == len(after) == 410
            assert json.loads(after[0].explain_json)["meta"]["feeBasis"] == "measured"
    finally:
        await engine.dispose()
