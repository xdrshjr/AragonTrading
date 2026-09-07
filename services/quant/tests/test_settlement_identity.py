"""持仓 epoch、精确观察与恢复的行为契约。"""
import pytest


def tracker(account=1, run=7):
    from app.live_auto.settlement_identity import IdentityConfig
    from app.live_auto.settlement_identity import SettlementIdentityTracker
    return SettlementIdentityTracker(IdentityConfig(
        account, run, "SOL-USDT-SWAP",
        {"tickSz": .01, "marginMode": "cross"}, {}))


def trade(number=1, side="long", qty=10, **changes):
    values = dict(id=number, runId=7, side=side, qty=qty, price=100,
                  fee=.5, pnl=0, wallTs=1000 + number,
                  accountingEventId=f"event-{number}",
                  feeBasis="measured", feeKnown=True)
    values.update(changes)
    return values


def observation(**changes):
    values = dict(instId="SOL-USDT-SWAP", posId="p1", posSide="net",
                  pos=10, avgPx=100, marginMode="cross",
                  exchangeCTimeMs="1000000", exchangeUTimeMs="1100001",
                  cTimeS=1000, uTimeS=1100, observedAtTs=1101)
    values.update(changes)
    return values


def test_partial_open_keeps_first_identity_and_wall_clock():
    unit = tracker()
    unit.record_trade(trade(qty=4))
    first = unit.context().epoch_key
    unit.record_trade(trade(2, qty=6, price=110))
    unit.record_trade(trade(2, qty=6, price=110))
    assert unit.context().epoch_key == first
    assert unit.context().total_open_qty == 10
    assert unit.context().entry_avg_px == 106
    assert unit.context().first_open_wall_ts == 1001


def test_partial_duplicate_and_missing_fee_full_close_use_quantity_boundary():
    unit = tracker()
    unit.record_trade(trade())
    unit.record_trade(trade(2, "close", 4))
    unit.record_trade(trade(2, "close", 4))
    assert unit.context().remaining_qty == 6
    unit.record_trade(trade(3, "close", 6, fee=None))
    assert unit.context() is None
    unit.record_trade(trade(4, fee=0))
    assert unit.context().booked_fee_usd == 0


def test_exact_observation_restores_only_same_epoch_and_conflict_is_sticky():
    unit = tracker()
    unit.record_trade(trade())
    unit.observe_position(observation())
    frozen = unit.context()
    unit.observe_position(observation(pos=999))
    unit.observe_position(observation())
    assert unit.context().ownership_conflict
    assert not frozen.ownership_conflict
    restored = tracker()
    restored.restore([trade()], frozen.observation_wire())
    assert restored.context().exchange_utime_ms == "1100001"
    restored.restore([trade(2)], frozen.observation_wire())
    assert restored.context().pos_id is None


def test_account_and_run_are_isolated():
    first, second = tracker(), tracker(account=2)
    first.record_trade(trade())
    second.record_trade(trade())
    assert first.context().epoch_key != second.context().epoch_key
    wrong = tracker()
    wrong.record_trade(trade(runId=8))
    assert wrong.context() is None


def test_missing_stable_identity_is_not_python_object_id():
    unit = tracker()
    leg = trade()
    del leg["id"], leg["accountingEventId"]
    unit.record_trade(leg)
    assert unit.context().epoch_key is None


def test_full_restore_collects_only_persisted_claims():
    unit = tracker()
    unit.record_trade(trade())
    epoch = unit.context().epoch_key
    close = trade(2, "close")
    close["explain"] = {"meta": {"settlementKey": "key",
        "settlementValidation": {"epochKey": epoch, "schemaVersion": 1,
                                 "status": "matched"},
        "accountingEventId": "close-event"}}
    unit.restore([trade(), close], None)
    assert unit.claimed["key"]["accountingEventId"] == "close-event"
    assert unit.context() is None


@pytest.mark.parametrize("invalid", ["legacy", "wrong_epoch",
                                    "partial", "other_instrument"])
def test_restore_does_not_claim_unverified_or_unclosed_epoch(invalid):
    unit = tracker()
    unit.record_trade(trade())
    epoch = unit.context().epoch_key
    validation = dict(schemaVersion=1, status="matched", epochKey=epoch)
    close = trade(2, "close", qty=4 if invalid == "partial" else 10)
    if invalid == "legacy":
        del validation["schemaVersion"]
    if invalid == "wrong_epoch":
        validation["epochKey"] = "other"
    if invalid == "other_instrument":
        close["instId"] = "ETH-USDT-SWAP"
    close["explain"] = {"meta": {"settlementKey": "key",
        "settlementValidation": validation, "accountingEventId": "saved"}}
    unit.restore([trade(), close], None)
    assert not unit.claimed


def test_late_observation_never_rolls_back_exchange_update_identity():
    unit = tracker()
    unit.record_trade(trade())
    unit.observe_position(observation())
    unit.observe_position(observation(exchangeUTimeMs="1099000"))
    assert unit.context().exchange_utime_ms == "1100001"


def test_changed_persisted_margin_mode_is_not_accepted_as_paused_identity():
    unit = tracker()
    unit.record_trade(trade())
    unit.observe_position(observation())
    saved = unit.observation_wire()
    saved["marginMode"] = "isolated"
    unit.restore([trade()], saved)
    assert unit.context().pos_id is None


def test_first_trustworthy_epoch_wall_clock_can_be_in_later_open_segment():
    unit = tracker()
    first = trade(qty=4)
    del first["wallTs"]
    unit.record_trade(first)
    unit.record_trade(trade(2, qty=6))
    assert unit.context().first_open_wall_ts == 1002
    assert unit.context().time_basis == "wall"


@pytest.mark.parametrize("mutation,expected", [({}, True),
    ({"qty": 4}, False), ({"runId": 8}, False),
    ({"instId": "ETH-USDT-SWAP"}, False), ({"wallTs": 900}, False),
    ({"positionAfter": 1}, False), ({"price": 0}, False),
    ({"posId": "other"}, False), ({"event": "offline:p1:1201"}, False)])
def test_legacy_alias_requires_same_epoch_scope_time_and_complete_quantity(
        mutation, expected):
    from app.live_auto import settlement_identity as identity
    unit = tracker()
    opened = trade()
    unit.record_trade(opened)
    close = trade(2, "close", wallTs=1200)
    close.update({key: value for key, value in mutation.items()
                  if key not in ("posId", "event")})
    close.pop("accountingEventId")
    close["explain"] = {"meta": {
        "accountingEventId": mutation.get("event", "offline:p1:1200"),
        "posId": mutation.get("posId", "p1"), "wallTs": close["wallTs"]}}
    assert identity.has_closed_offline_epoch(
        [opened, close, close], unit.config, unit.context().epoch_key,
        "p1") is expected
    assert not identity.has_closed_offline_epoch(
        [opened, close], unit.config, "other-epoch", "p1")


def test_closed_epoch_contexts_use_structural_sequence_and_remaining_quantity():
    from app.live_auto import settlement_identity as identity
    rows = [trade(), trade(2, "close", 4), trade(3, "close", 6, fee=None),
            trade(4), trade(5, "close"), trade(5, "close")]
    contexts = identity.closed_epoch_contexts(rows, tracker().config)
    assert [item.remaining_qty for item in contexts] == [6, 10]
    assert [item.first_open_wall_ts for item in contexts] == [1001, 1004]
    assert contexts[0].epoch_key != contexts[1].epoch_key


def test_adopt_newer_durable_observation_keeps_derived_pool_and_conflict():
    unit = tracker()
    unit.record_trade(trade())
    unit.observe_position(observation())
    durable = unit.observation_wire()
    durable.update(exchangeUTimeMs="1100002", observedAtTs=1102)
    assert unit.adopt_observation(durable)
    assert unit.context().exchange_utime_ms == "1100002"
    assert unit.context().booked_fee_usd == .5
    assert not unit.adopt_observation({**durable, "observedAtTs": 1000})
    unit.mark_conflict()
    assert unit.adopt_observation(durable)
    assert unit.context().ownership_conflict
    assert not unit.adopt_observation({**durable, "epochKey": "other"})


def test_older_wall_observation_does_not_replace_newer_context():
    unit = tracker()
    unit.record_trade(trade())
    unit.observe_position(observation())
    unit.observe_position(observation(exchangeUTimeMs="1100002",
                                      observedAtTs=1050))
    assert unit.context().exchange_utime_ms == "1100001"


@pytest.mark.parametrize("change", [{"posId": "other"},
                                     {"exchangeCTimeMs": "999000"}])
def test_durable_observation_cannot_switch_exchange_identity_in_same_epoch(change):
    unit = tracker()
    unit.record_trade(trade())
    unit.observe_position(observation())
    saved = {**unit.observation_wire(), **change}
    assert not unit.adopt_observation(saved)
    assert unit.context().ownership_conflict
    assert unit.context().pos_id == "p1"
    assert unit.context().exchange_ctime_ms == "1000000"


def test_close_with_contradictory_remaining_position_never_ends_epoch():
    unit = tracker()
    unit.record_trade(trade())
    unit.record_trade(trade(2, "close", positionAfter=1))
    assert unit.context() is not None
    assert unit.context().ownership_conflict


def test_close_quantity_tolerance_uses_same_zero_boundary_as_accounting():
    unit = tracker()
    unit.record_trade(trade())
    unit.record_trade(trade(2, "close", qty=10 - 1e-9, positionAfter=0))
    assert unit.context() is None
