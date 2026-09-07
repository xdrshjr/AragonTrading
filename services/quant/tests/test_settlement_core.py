"""结算身份、候选集合、金额恒等式和累计增量的行为契约。"""
import pytest

from app.live_auto import settlement
from app.live_auto.settlement_identity import SettlementContext


def context(**changes):
    values = dict(account_id=1, run_id=7, inst_id="SOL-USDT-SWAP",
                  epoch_key="epoch-1", direction="long", margin_mode="cross",
                  pos_side="net", pos_id="p1", exchange_ctime_ms="1000000",
                  exchange_utime_ms="1100000", observed_at_ts=1100,
                  first_open_wall_ts=1000, total_open_qty=10, closed_qty=0,
                  remaining_qty=10, entry_avg_px=100, tick_sz=.01,
                  booked_fee_usd=.5, booked_close_pnl=0,
                  booked_close_qty_price=0, fee_basis="estimated",
                  direction_confirmed=True)
    values.update(changes)
    return SettlementContext(**values)


def row(**changes):
    values = dict(instId="SOL-USDT-SWAP", posId="p1", direction="long",
                  instType="SWAP", currency="USDT", closed=True, type="2",
                  marginMode="cross", exchangeCTimeMs="1000000",
                  exchangeUTimeMs="1200001", openAvgPx=100, closeAvgPx=101,
                  closeTotalPos=10, pnl=10, fee=.2, fundingFee=0,
                  liqPenalty=0, settledPnl=0, realizedPnl=9.8)
    values.update(changes)
    return values


def query(**changes):
    values = dict(inst_id="SOL-USDT-SWAP", direction="long", entry_ts=1000,
                  detect_ts=1200, context=context(), tol_s=60)
    values.update(changes)
    return settlement.SettlementQuery(**values)


def validate(rows=None, **changes):
    return settlement.validate_settlement(
        [row()] if rows is None else rows, query(**changes), {}, 1205)


def test_legacy_query_cannot_accept_unknown_identity():
    legacy = settlement.SettlementQuery("SOL-USDT-SWAP", "long", 1000, 1200)
    assert settlement.match_settlement(
        [row()], legacy, claimed=set(), tol_s=60) is None


def test_complete_row_matches_and_wire_hides_internal_fact():
    result = validate()
    assert result.status == "matched"
    assert result.fact.gross_pnl_usd == 10
    assert result.wire()["amountCoverage"] == "complete"
    assert "fact" not in result.wire()


@pytest.mark.parametrize("field", ["pnl", "fee", "fundingFee",
                                   "liqPenalty", "realizedPnl"])
@pytest.mark.parametrize("value,reason", [(None, "missing_amount"),
    (True, "invalid_amount"), (float("nan"), "invalid_amount"),
    (float("inf"), "invalid_amount")])
def test_unknown_and_invalid_amounts_are_never_zero(field, value, reason):
    result = validate([row(**{field: value})])
    assert result.fact is None and result.reason == reason


@pytest.mark.parametrize("change,reason", [
    ({"instType": "FUTURES"}, "unsupported_product"),
    ({"currency": None}, "currency_unknown"),
    ({"currency": "BTC"}, "unsupported_currency"),
    ({"closed": False, "type": "1"}, "not_closed"),
    ({"marginMode": ""}, "identity_unavailable"),
    ({"marginMode": "isolated"}, "identity_mismatch"),
    ({"exchangeCTimeMs": "999000"}, "identity_mismatch"),
    ({"exchangeUTimeMs": "1100000"}, "time_mismatch"),
    ({"exchangeUTimeMs": None}, "identity_unavailable"),
    ({"closeTotalPos": 999}, "quantity_mismatch"),
    ({"openAvgPx": 99}, "invalid_price"),
    ({"closeAvgPx": 0}, "invalid_price"),
    ({"settledPnl": 1}, "invalid_amount"),
    ({"realizedPnl": 0, "identityGapUsd": 0}, "identity_failed"),
])
def test_rejects_wrong_scope_and_recomputes_amount_identity(change, reason):
    assert validate([row(**change)]).reason == reason


def test_duplicate_pages_deduplicate_but_conflicting_contents_fail():
    assert validate([row(), row(closeTotalPos=10.0)]).status == "matched"
    assert validate([row(), row(pnl=11, realizedPnl=10.8)]).reason == (
        "conflicting_duplicate")


def test_same_second_different_millisecond_candidates_are_ambiguous():
    assert validate([row(), row(exchangeUTimeMs="1200002")]).reason == (
        "ambiguous")


def test_unrelated_bad_rows_do_not_hide_unique_valid_candidate():
    bad = row(posId="unrelated", fee=None)
    assert validate([bad, row()]).status == "matched"
    assert validate([row(), bad]).status == "matched"


def test_failure_reason_is_independent_of_input_order():
    bad = row(fee=None)
    other = row(closeTotalPos=999)
    assert validate([bad, other]).reason == validate([other, bad]).reason


def test_claims_are_keys_scoped_to_epochs_not_position_ids():
    fact = validate().fact
    claimed = {fact.settlement_key: {"epochKey": "epoch-1",
                                   "accountingEventId": "saved"}}
    result = settlement.validate_settlement([row()], query(), claimed, 1205)
    assert result.status == "already_claimed"
    newer = row(exchangeUTimeMs="1200002")
    result = settlement.validate_settlement([row(), newer], query(), claimed,
                                            1205)
    assert result.status == "matched"
    assert result.fact.settlement_key != fact.settlement_key


def test_direction_missing_requires_fresh_observation_direction_evidence():
    assert validate([row(direction="")]).status == "matched"
    assert validate([row(direction="")], context=context(
        direction_confirmed=False)).reason == "identity_unavailable"


def test_offline_boundary_uses_paused_timestamp_and_requires_it():
    assert validate(mode="offline").reason == "time_mismatch"
    assert validate(mode="offline", paused_ts=1200).status == "matched"
    assert validate(mode="offline", paused_ts=1300).reason == "time_mismatch"


@pytest.mark.parametrize("basis,pnl", [("gross", 10), ("net", 9.8)])
def test_negative_residual_retains_estimated_fee_source(basis, pnl):
    result = settlement.resolve_settlement_increment(
        validate().fact, context(), basis)
    leg = result.increment
    assert leg.fee == pytest.approx(-.3)
    assert leg.pnl == pytest.approx(pnl)
    assert leg.fee_basis == "estimated"
    assert leg.fee_adjustment["kind"] == "aggregate_residual"
    assert leg.accounting_event_id.startswith("settlement:7:epoch-1:")


def test_partial_close_resolves_final_price_and_money_only_once():
    ctx = context(closed_qty=4, remaining_qty=6, booked_close_qty_price=440,
                  booked_close_pnl=40, booked_fee_usd=.6)
    fact = validate([row(closeAvgPx=116, pnl=160, realizedPnl=159.8)],
                    context=ctx).fact
    increment = settlement.resolve_settlement_increment(
        fact, ctx, "gross").increment
    assert (increment.qty, increment.price, increment.pnl) == (6, 120, 120)
    assert increment.fee == pytest.approx(-.4)
    assert increment.price_basis == "exchange_aggregate_residual"


@pytest.mark.parametrize("change", [dict(booked_fee_usd=None),
    dict(booked_close_pnl=None), dict(booked_close_qty_price=float("inf")),
    dict(remaining_qty=0)])
def test_unverifiable_increment_does_not_create_trade(change):
    result = settlement.resolve_settlement_increment(
        validate().fact, context(**change), "net")
    assert result.increment is None and result.reason == "increment_invalid"


def test_meta_and_coverage_preserve_known_zero_and_unknown_absence():
    wire = settlement.settlement_meta_wire(
        None, mark_px=0, waited_s=7, booked_open_fee_usd=float("nan"))
    assert wire["settlementSource"] == "local_mark"
    assert "exchangeGrossPnlUsd" not in wire
    assert settlement.coverage_wire(0, 0) is None
    assert settlement.coverage_wire(3, 1)["ratio"] == .75
    wire = settlement.settlement_meta_wire(
        validate().fact, mark_px=100, waited_s=7, booked_open_fee_usd=.5)
    assert wire["exchangeFundingUsd"] == 0
    assert wire["exchangeRealizedPnlUsd"] == 9.8


def test_garbage_rows_do_not_raise():
    assert validate([None, {}, 12, {"posId": object()}]).reason == "not_found"


def test_settlement_meta_keeps_unrounded_small_amounts_for_replay():
    fact = validate([row(pnl=.00000011, fee=.00000002,
                         realizedPnl=.00000009)]).fact
    wire = settlement.settlement_meta_wire(
        fact, mark_px=100, waited_s=1, booked_open_fee_usd=.00000001)
    assert wire["exchangeGrossPnlUsd"] == .00000011
    assert wire["exchangeFeeUsd"] == .00000002
    assert wire["bookedOpenFeeUsd"] == .00000001


@pytest.mark.parametrize("change", [dict(account_id=True),
    dict(run_id=False), dict(observed_at_ts=True)])
def test_boolean_identity_scope_and_observation_are_not_verified(change):
    assert validate(context=context(**change)).fact is None


def test_malformed_context_reports_unavailable_without_raising():
    assert validate(context=object()).reason == "identity_unavailable"
