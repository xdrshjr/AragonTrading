"""资金费证据闭环：预测、计提和成本只消费当时可见证据。"""
import pytest

from app.ai_auto.cost_model import (
    CostParams, CostQuery, cost_wire, estimate_round_trip_cost,
    query_for_preview,
)
from app.ai_auto.funding import FundingSnapshot
from app.ai_auto.funding_ledger import BacktestFundingCurve, FundingLedger


def point(ts, rate=0.0001):
    return {"fundingTs": ts, "realizedRate": rate}


def test_future_append_cannot_change_snapshot_or_cost():
    rows = [point(28800), point(57600)]
    before = BacktestFundingCurve(rows).snapshot_at(57600)
    after = BacktestFundingCurve(rows + [point(61200)]).snapshot_at(57600)
    assert before == after
    assert before.interval_s == 28800
    assert before.interval_known is False
    assert before.interval_source == "historical_observed"
    assert _cost(before) == _cost(after)


def _cost(snapshot):
    query = query_for_preview(
        ord_type="market", side="long", bar_seconds=3600,
        funding=snapshot, funding_enabled=True)
    return cost_wire(estimate_round_trip_cost(
        query, CostParams(2, 5, 1, 0, 24)))


def test_one_visible_point_stays_unavailable_with_future_rows():
    assert BacktestFundingCurve(
        [point(100), point(200), point(300)]).snapshot_at(150) is None


def test_expired_prefix_waits_for_new_visible_point():
    curve = BacktestFundingCurve([point(100), point(200), point(500)])
    assert curve.snapshot_at(300) is not None
    assert curve.snapshot_at(301) is None
    assert curve.snapshot_at(500).interval_s == 300


@pytest.mark.parametrize("bad", [None, "", True, float("nan"), float("inf")])
def test_invalid_timed_point_breaks_prefix_and_recovers_after_two(bad):
    curve = BacktestFundingCurve([
        point(100), point(200), point(250, bad), point(300), point(400)])
    assert curve.snapshot_at(200) is not None
    assert curve.snapshot_at(250) is None
    assert curve.snapshot_at(300) is None
    assert curve.snapshot_at(400).interval_s == 100
    assert curve.invalid_rows == 1


def test_conflicting_points_are_order_independent_breaks():
    rows = [point(100), point(200, 0), point(200, -0.0001)]
    for ordered in (rows, list(reversed(rows))):
        curve = BacktestFundingCurve(ordered)
        assert curve.snapshot_at(200) is None
        assert curve.points_in(100, 200) == []
        assert curve.conflicting_points == 1


def test_duplicate_zero_negative_realized_points_accrue_once():
    curve = BacktestFundingCurve([
        point(100, 0), point(100, 0), point(200, -0.0001)])
    assert curve.points_in(0, 200) == [(100, 0), (200, -0.0001)]
    assert curve.invalid_rows == 0


def test_estimated_rate_predicts_but_never_settles():
    curve = BacktestFundingCurve([
        {"fundingTs": 100, "fundingRate": 0.001},
        {"fundingTs": 200, "fundingRate": 0.002}])
    assert curve.snapshot_at(200).rate == 0.002
    assert curve.points_in(0, 200) == []
    evidence = curve.window_evidence(100, 200)
    assert evidence["coverage"] != "complete"
    assert evidence["estimatedPointCount"] == 1


def test_untimed_rows_only_affect_final_diagnostics():
    rows = [point(100), point(200)]
    before = BacktestFundingCurve(rows)
    after = BacktestFundingCurve(rows + [point(None), point(400, None)])
    assert before.snapshot_at(200) == after.snapshot_at(200)
    assert before.window_evidence(150, 200) == after.window_evidence(150, 200)
    assert after.untimed_invalid_rows == 1


def test_single_realized_point_accrues_known_amount_without_future():
    outcomes = []
    for rows in ([point(100, 0.001)],
                 [point(100, 0.001), point(200), point(300, None)]):
        ledger = FundingLedger(enabled=True, ct_val=1)
        ledger.note_open({"side": "long", "qty": 2}, seq=1, now_wall=50)
        ledger.accrue_bars(BacktestFundingCurve(rows), from_ts=50,
                           to_ts=100, mark_px=1000)
        assert ledger.crossings == 1
        stats = ledger.stats(settled_usd=None)
        assert stats["knownAccruedUsd"] == 2
        assert stats["fundingCostUsd"] is None
        assert stats["intervalKnown"] is False
        outcomes.append(stats)
    assert outcomes[0] == outcomes[1]


def test_past_gap_survives_recovery_and_close():
    ledger = FundingLedger(enabled=True, ct_val=1)
    ledger.note_open({"side": "long", "qty": 1}, seq=1, now_wall=200)
    curve = BacktestFundingCurve([
        point(100), point(200), point(250, None), point(300), point(400)])
    ledger.accrue_bars(curve, from_ts=200, to_ts=250, mark_px=1000)
    ledger.accrue_bars(curve, from_ts=250, to_ts=400, mark_px=1000)
    assert curve.snapshot_at(400) is not None
    stats = ledger.stats(settled_usd=None)
    assert stats["knownAccruedUsd"] == pytest.approx(0.2)
    assert stats["accruedOpenUsd"] is None
    ledger.note_close({}, 400)
    stats = ledger.stats(settled_usd=None)
    assert stats["fundingCostUsd"] is None
    assert stats["closedCoverage"] == "partial"


def test_observed_anchor_can_prove_zero_only_until_expiry():
    curve = BacktestFundingCurve([point(100), point(200)])
    assert curve.window_evidence(200, 250)["coverage"] == "complete"
    assert curve.window_evidence(200, 301)["coverage"] != "complete"
    assert curve.window_evidence(10, 50)["coverage"] == "unavailable"


def test_unknown_interval_is_never_upgraded_by_query_adapter():
    snapshot = FundingSnapshot(0, 100, 200, 100, 0, False)
    query = query_for_preview(
        ord_type="market", side="long", bar_seconds=3600,
        funding=snapshot, funding_enabled=True)
    assert query.funding_interval_known is False
    assert _cost(snapshot)["confidence"] == "degraded"


@pytest.mark.parametrize("interval", [-1, 0, float("nan"), True])
def test_bad_interval_uses_same_fallback_for_math_and_wire(interval):
    cost = estimate_round_trip_cost(CostQuery(
        "market", "long", 3600, funding_rate=0.0001,
        funding_interval_s=interval, funding_interval_known=True),
        CostParams(2, 5, 1, 0, 24))
    assert cost.funding_periods == 3
    assert cost.funding_carry_bps == 3
    assert cost.confidence == "degraded"
    assert cost.funding_interval_s is None
    assert cost_wire(cost)["fundingAssumedIntervalS"] == 28800


def test_explicit_disable_ignores_nonzero_rate_and_interval():
    query = CostQuery("market", "long", 3600, funding_rate=1,
                      funding_enabled=False, funding_interval_known=True)
    cost = estimate_round_trip_cost(query, CostParams(2, 5, 1, 0, 24))
    assert cost.total_bps == 12
    assert cost.funding_carry_bps == 0
    assert cost.funding_periods is None
    assert cost.funding_interval_s is None
    assert cost.funding_assumed_interval_s is None
    assert cost.model_version == 3


def _runner(settings, rest=None):
    from app.ai_auto.backtest import AiBacktestRunner
    runner = object.__new__(AiBacktestRunner)
    runner.settings = settings
    runner.rest = rest
    runner.inst_id = "BTC-USDT-SWAP"
    runner.start_ts, runner.end_ts = 300, 500
    runner._funding = FundingLedger(enabled=False, ct_val=1)
    runner._curve = BacktestFundingCurve([])
    runner._round_cost = None
    runner._horizon_hold_bars = 24
    return runner


@pytest.mark.parametrize("cost_on,ledger_on,expected_calls", [
    (True, False, 2), (False, True, 2), (False, False, 0)])
async def test_curve_load_switches_are_independent_and_warm_two_events(
        cost_on, ledger_on, expected_calls):
    from app.config import Settings
    class Rest:
        calls = []

        async def get_funding_rate_history(self, _inst_id, **kwargs):
            self.calls.append(kwargs)
            return ([point(400), point(300)] if len(self.calls) == 1
                    else [point(200), point(100)])

    rest = Rest()
    runner = _runner(Settings(
        ai_trade_funding_cost_backtest_enabled=cost_on,
        ai_trade_funding_ledger_enabled=ledger_on,
        ai_trade_funding_backtest_enabled=True), rest)
    runner._funding.enabled = ledger_on
    await runner._load_funding_curve()
    assert len(rest.calls) == expected_calls
    if expected_calls:
        assert rest.calls[1]["after_ms"] == 300000
        assert runner._curve.snapshot_at(300).interval_s == 100


async def test_invalid_or_repeated_page_terminates_with_partial_coverage():
    from app.config import Settings
    class Rest:
        calls = 0

        async def get_funding_rate_history(self, *_args, **_kwargs):
            self.calls += 1
            return [point(400), point(None)]

    rest = Rest()
    runner = _runner(Settings(), rest)
    runner._funding.enabled = True
    await runner._load_funding_curve()
    assert rest.calls == 2
    assert runner._curve.load_coverage == "partial"
    assert runner._curve.points_in(300, 500) == [(400, 0.0001)]


def test_missing_curve_retains_enabled_state():
    from app.config import Settings
    assert _runner(Settings())._cost_funding(300) == (None, True)


async def test_real_decision_counts_include_model_failure(monkeypatch):
    from app.db.session import dispose_engine
    from tests.test_ai_trade_backtest_funding_cost import _run, _settings

    async def fail(_self, _payload):
        raise RuntimeError("用于验证模型失败前已记录轮次")

    from app.ai_auto.decider import AiTradeDecider
    monkeypatch.setattr(AiTradeDecider, "decide", fail)
    try:
        runner = await _run(monkeypatch, settings=_settings())
        evidence = runner._aggregate_stats()["fundingEvidence"]
        assert evidence["decisionRounds"] == 1
        assert evidence["enabledRounds"] == 1
        assert evidence["degradedRounds"] == 1
        assert (evidence["visibleRateRounds"] + evidence["missingRounds"]
                == evidence["enabledRounds"])
    finally:
        await dispose_engine()


def test_preview_and_repeated_decision_entry_do_not_inflate_counts():
    from app.ai_auto.backtest_funding import FundingDecisionEvidence
    from app.config import Settings
    runner = _runner(Settings())
    runner._funding_evidence = FundingDecisionEvidence()
    runner._curve = BacktestFundingCurve([point(100), point(200)])
    for _ in range(3):
        runner._begin_round_cost(200)
    assert runner._funding_evidence.wire(runner._curve)["decisionRounds"] == 0
    for _ in range(3):
        runner._begin_round_cost(200, decision_id=(1, 200))
    runner._begin_round_cost(500, decision_id=(2, 500))
    runner.settings.ai_trade_funding_cost_backtest_enabled = False
    runner._begin_round_cost(600, decision_id=(3, 600))
    evidence = runner._funding_evidence.wire(runner._curve)
    assert {key: evidence[key] for key in (
        "decisionRounds", "enabledRounds", "disabledRounds",
        "visibleRateRounds", "missingRounds", "degradedRounds")
    } == dict(decisionRounds=3, enabledRounds=2, disabledRounds=1,
              visibleRateRounds=1, missingRounds=1, degradedRounds=2)


async def test_loading_failure_preserves_known_points_and_untimed_gap_is_global():
    from app.ai_auto.backtest_funding import load_history_curve
    class Rest:
        calls = 0

        async def get_funding_rate_history(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("离线请求失败替身")
            return [point(100), point(200)]

    baseline = BacktestFundingCurve([point(100), point(200)])
    curve = await load_history_curve(Rest(), dict(
        inst_id="X", start_ts=100, end_ts=250, max_pages=5))
    assert curve.load_coverage == "partial"
    assert curve.points_in(0, 200) == baseline.points_in(0, 200)
    assert curve.window_evidence(200, 250) == baseline.window_evidence(200, 250)


def test_located_loading_gap_affects_only_overlapping_past_window():
    rows = [point(100), point(200)]
    curve = BacktestFundingCurve(rows, load_gaps=[(210, 220)])
    assert curve.window_evidence(200, 205)["coverage"] == "complete"
    assert curve.window_evidence(200, 250)["coverage"] == "partial"


async def test_three_model_retries_keep_one_frozen_cost_per_round(monkeypatch):
    from app.ai_auto.decider import AiTradeDecider, TradeDecision
    from app.db.session import dispose_engine
    from tests.test_ai_trade_backtest_funding_cost import _run, _settings
    attempts = {}

    async def attempt(_self, payload, _emit=None, **_kwargs):
        receipts = attempts.setdefault(payload["round"], [])
        receipts.append(payload["costAwareness"])
        reason = "llm_error" if len(receipts) < 4 else None
        return TradeDecision("hold", None, None, "离线重试验证"), reason

    async def no_backoff(*_args):
        pass

    monkeypatch.setattr(AiTradeDecider, "_attempt_decision", attempt)
    monkeypatch.setattr(AiTradeDecider, "_retry_backoff", no_backoff)
    try:
        runner = await _run(monkeypatch, settings=_settings(
            ai_trade_reasoning_depth_backtest="off",
            ai_trade_decision_max_retries=3))
        evidence = runner._aggregate_stats()["fundingEvidence"]
        assert len(attempts) == evidence["decisionRounds"] > 0
        for receipts in attempts.values():
            assert len(receipts) == 4
            assert all(receipt == receipts[0] for receipt in receipts)
            assert receipts[0]["modelVersion"] == 3
    finally:
        await dispose_engine()


async def test_future_rows_do_not_change_full_backtest_decisions(monkeypatch):
    from app.ai_auto.backtest import AiBacktestRunner
    from app.db.session import dispose_engine
    from tests.conftest import make_synthetic_candles
    from tests.test_ai_trade_backtest_funding_cost import (
        _curve_rows, _fingerprint, _run, _settings,
    )
    rows = _curve_rows(make_synthetic_candles(200))
    future = rows[-1]["fundingTs"] + 100000
    outcomes = []
    captured = []
    original = AiBacktestRunner._build_payload

    def capture(runner, *args, **kwargs):
        payload = original(runner, *args, **kwargs)
        captured.append((payload.get("position"),
                         payload.get("costAwareness"),
                         runner._acceptance_bundle()))
        return payload

    monkeypatch.setattr(AiBacktestRunner, "_build_payload", capture)
    for appended in ([], [point(future), point(future + 100, None), point(None)]):
        try:
            captured.clear()
            runner = await _run(monkeypatch, settings=_settings(), rows=rows + appended)
            outcomes.append((_fingerprint(runner), runner._acceptance_bundle(),
                             runner._round_cost, list(captured)))
        finally:
            await dispose_engine()
    assert outcomes[0] == outcomes[1]
    assert any(payload[0] is not None for payload in outcomes[0][-1])
