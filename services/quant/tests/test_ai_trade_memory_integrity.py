"""当前事实与历史叙述隔离，折叠不伪造证据。"""
from copy import deepcopy

import json
import pytest

from app.ai_auto.prompts import compact_journal_for_summary


def entry(round_no=1):
    from app.ai_auto.advisory_evidence import bound_summary_evidence
    from app.ai_auto.prompts import build_advisory_evidence
    scope = {"runId": 71, "instId": "BTC-USDT-SWAP", "bar": "1m"}
    evidence = bound_summary_evidence(build_advisory_evidence(
        {**scope, "round": round_no}))
    return {"round": round_no, "trigger": "interval",
            "evidenceVersion": 4, **evidence,
            "proposal": {"action": "hold"},
            "decision": {"action": "hold"}, "finalDecision": {"action": "hold"},
            "executed": {"ok": True, "traded": False},
            "facts": {**scope, "asOfTs": round_no, "position": None},
            "evidenceFingerprint": "same", "reflection": None}


def test_gate_veto_and_legacy_rounds_never_fold():
    left, right = entry(), entry(2)
    right["gateVeto"] = {"gate": "consensus"}
    assert len(compact_journal_for_summary([left, right])) == 2
    left.pop("evidenceFingerprint")
    right = deepcopy(left)
    assert len(compact_journal_for_summary([left, right])) == 2


def test_identical_evidence_fold_preserves_range_and_input():
    rounds = [entry(), entry(2)]
    original = deepcopy(rounds)
    folded = compact_journal_for_summary(rounds)
    assert len(folded) == 1
    assert folded[0]["firstAsOfTs"] == 1
    assert folded[0]["lastAsOfTs"] == 2
    assert folded[0]["evidenceFingerprint"] == "same"
    assert rounds == original


@pytest.mark.parametrize("patch", [
    {"proposal": {"action": "open_short"}},
    {"executionReceipt": {"status": "unknown"}},
    {"executed": {"ok": False, "error": "timeout"}},
    {"sizingVerdict": {"zeroed": True}},
    {"sizingVerdict": {"floorVerdict": "below_gear_floor"}},
    {"executed": {"ok": True, "action": "close", "traded": False}},
    {"reflection": {"vetoed": True}},
    {"evidenceFingerprint": "changed-price"},
    {"facts": {"position": {"epoch": 3}}},
])
def test_fold_preserves_every_material_intervention(patch):
    assert len(compact_journal_for_summary([entry(), {**entry(2), **patch}])) == 2


@pytest.mark.asyncio
async def test_commit_records_proposal_veto_and_exact_audit_tool_result():
    from app.ai_auto.decision_memory import RoundDecisionRecord, build_decision_facts
    from app.ai_auto.decider import TradeDecision
    from .test_ai_trade_decider import _decider, _payload
    payload = _payload(1)
    payload["decisionFacts"] = build_decision_facts({"round": 1, "asOfTs": 10})
    final = TradeDecision("hold", None, None, "门禁否决")
    record = RoundDecisionRecord({"action": "open_short"},
        {"action": "hold"}, payload["decisionFacts"])
    decider = _decider([])
    veto = {"gate": "consensus", "reason": "同向票不足"}
    await decider.commit_round(payload, final, {"ok": True, "traded": False},
                               record=record, gate_veto=veto)
    wire = json.loads(decider._context[-1].content[0].content)
    assert wire["proposal"]["action"] == "open_short"
    assert wire["gateVeto"] == veto
    assert decider._journal[-1]["gateVeto"] == veto
    assert wire["factsHash"] == payload["decisionFacts"]["factsHash"]
    assert decider._context[-1].role == "tool"


@pytest.mark.asyncio
async def test_summary_failure_keeps_legacy_background_and_current_facts():
    from app.ai_auto.decision_memory import build_decision_facts
    from app.ai_auto.prompts import build_decision_prompt
    from .test_ai_trade_decider import _decider, _payload
    decider = _decider([])
    decider.seed_from_snapshot(summary="历史通道0.00故障", last_round=9)
    assert decider.memory_wire()["legacyNarrative"] is True
    assert await decider.summarize() is None
    assert decider.summary == "历史通道0.00故障"
    payload = _payload(10)
    payload["summary"] = decider.summary
    payload["decisionFacts"] = build_decision_facts({
        "round": 10, "position": {"avgEntry": 1e-8}, "asOfTs": 11})
    text = build_decision_prompt(payload)
    assert text.index("本轮程序事实") < text.index(decider.summary)




def test_facts_hash_changes_with_time_but_evidence_only_with_material_facts():
    from app.ai_auto.decision_memory import build_decision_facts
    payload = {"runId": 1, "instId": "SATS", "bar": "1m", "round": 1,
               "asOfTs": 10, "lastPx": 1e-8, "barTs": 1,
               "position": None, "costSnapshot": {"stale": False}}
    first = build_decision_facts(payload)
    second = build_decision_facts({**payload, "round": 2, "asOfTs": 20})
    assert first["factsHash"] != second["factsHash"]
    assert first["evidenceFingerprint"] == second["evidenceFingerprint"]
    for key, value in (("lastPx", 2e-8), ("barTs", 2),
                       ("costSnapshot", {"stale": True}),
                       ("rules", {"version": 3}),
                       ("position", {"epoch": 2}),
                       ("entryQuota", {"remaining": 0})):
        changed = build_decision_facts({**payload, key: value})
        assert changed["evidenceFingerprint"] != first["evidenceFingerprint"]


@pytest.mark.parametrize("key, first, changed", [
    ("intel", {"text": "利多"}, {"text": "利空"}),
    ("intel", {"trusted": True}, {"trusted": False}),
    ("intel", {"references": ["I01"]}, {"references": ["I02"]}),
    ("experience", {"sampleN": 2}, {"sampleN": 3}),
    ("experience", {"text": "减仓"}, {"text": "平仓"}),
    ("signalPriors", {"netStance": "long"}, {"netStance": "short"}),
    ("signalPriors", {"ageS": 60}, {"ageS": 90}),
    ("intel", {"updatedTs": 10}, {"updatedTs": 20}),
    ("runId", 1, 2),
])
def test_advisory_wire_changes_are_material_evidence(key, first, changed):
    from app.ai_auto.decision_memory import build_evidence_fingerprint
    baseline = {"runId": 1, "instId": "BTC", "lastPx": 100, key: first}
    assert build_evidence_fingerprint(baseline) != (
        build_evidence_fingerprint({**baseline, key: changed}))


@pytest.mark.parametrize("version", [None, 2, 3, 99])
def test_non_v4_rounds_never_fold(version):
    left, right = entry(), entry(2)
    left["evidenceVersion"] = right["evidenceVersion"] = version
    assert len(compact_journal_for_summary([left, right])) == 2


@pytest.mark.parametrize("key", ["runId", "instId", "bar"])
def test_identical_text_never_folds_across_scope(key):
    left, right = entry(), entry(2)
    left["facts"][key] = "范围一"
    right["facts"][key] = "范围二"
    assert len(compact_journal_for_summary([left, right])) == 2


@pytest.mark.parametrize("section", ["facts", "advisoryEvidence"])
@pytest.mark.parametrize("key", ["runId", "instId", "bar"])
@pytest.mark.parametrize("value", [None, ""])
def test_unknown_scope_cannot_prove_rounds_equivalent(section, key, value):
    left, right = entry(), entry(2)
    left[section][key] = right[section][key] = value
    assert len(compact_journal_for_summary([left, right])) == 2


@pytest.mark.parametrize("patch", [
    {"omitted": True}, {"sourceHash": "新来源"},
    {"renderedHash": "新原文"},
])
def test_changed_or_omitted_advisory_never_folds(patch):
    left, right = entry(), entry(2)
    right["advisoryEvidence"]["intel"].update(patch)
    assert len(compact_journal_for_summary([left, right])) == 2


def test_facts_budget_removes_optional_text_before_failing_closed():
    from app.ai_auto.decision_memory import build_decision_facts
    optional = build_decision_facts({"position": {"avgEntry": 1e-8},
                                    "narrative": "长" * 9000})
    assert optional["position"]["avgEntry"] == 1e-8
    assert "narrative" not in optional
    unavailable = build_decision_facts({"pendingOpen": {"id": "a" * 9000}})
    assert unavailable["factsUnavailable"] is True
    assert len(json.dumps(unavailable, ensure_ascii=False)) < 8192


@pytest.mark.asyncio
async def test_actual_retry_and_reflection_keep_original_facts_hash():
    from app.agent.llm.base import StreamEvent
    from app.ai_auto.decider import AiTradeDecider
    from app.ai_auto.decision_memory import build_decision_facts
    from app.config import Settings

    payload = {"round": 1, "lastPx": 1e-8, "position": None,
               "summary": "旧通道0.00故障"}
    facts = build_decision_facts({"instId": "SATS", "round": 1,
                                  "asOfTs": 10, "position": None})
    payload["decisionFacts"] = deepcopy(facts)

    class MutatingLLM:
        texts = []

        async def stream(self, *, system, messages, tools):
            self.texts.append(messages[-1].content[0].text)
            payload["decisionFacts"]["factsHash"] = "外部新状态"
            if len(self.texts) == 1:
                yield StreamEvent("message_done", stop_reason="end")
                return
            name = tools[0].name
            args = ({"verdict": "confirm", "reason": "通过"}
                    if "reflection" in name else
                    {"action": "open_long", "sizeFraction": .2,
                     "confidence": .9, "reason": "证据"})
            yield StreamEvent("tool_use_start", id="t", name=name)
            yield StreamEvent("tool_use_args_delta", id="t",
                              partial_json=json.dumps(args))
            yield StreamEvent("message_done", stop_reason="tool_use")

    llm = MutatingLLM()
    decider = AiTradeDecider(llm, "sys", context_refresh_rounds=10,
        reflection_enabled=True, settings=Settings(
            ai_trade_decision_max_retries=1,
            ai_trade_decision_retry_backoff_base_s=0))
    decision, _ = await decider.decide(payload)
    await decider.reflect(payload, decision)
    assert len(llm.texts) == 3
    for text in llm.texts:
        assert facts["factsHash"] in text
        assert "外部新状态" not in text
        assert text.index(facts["factsHash"]) < text.index("旧通道0.00故障")


def _integrity_backtest(monkeypatch, action="open_long"):
    """组装真实 runner/SimBroker/决策器，只有 LLM 和外部取数使用桩。"""
    from dataclasses import replace
    from app.ai_auto.backtest import AiBacktestRunner
    from app.engine.backtester import candles_to_df
    from .test_ai_backtest_runner import _StubLLM, _spec
    from .conftest import make_synthetic_candles
    candles = make_synthetic_candles(90)
    llm = _StubLLM(lambda _: {"action": action, "sizeFraction": .5,
                             "confidence": .9, "reason": "集成事实测试"})
    monkeypatch.setattr("app.ai_auto.backtest.make_llm_client_from_config",
                        lambda cfg: llm)
    spec = replace(_spec(1, None, candles), specs=[], strategy_ids=[],
                   strategy_names=[])
    return AiBacktestRunner(spec), candles_to_df(candles)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["open_long", "close"])
async def test_backtest_unavailable_facts_block_only_new_exposure(monkeypatch, action):
    from app.ai_auto.backtest import _BacktestJob
    from app.ai_auto.decision_memory import build_decision_facts
    from app.ai_auto.signals import TradeDecision
    runner, frame = _integrity_backtest(monkeypatch, action)
    if action == "close":
        runner._execute(TradeDecision("open_long", .5, .9, "测试"))
        runner.main.fill_pending(100, int(frame.iloc[59]["ts"]), 59)
    build = runner._build_payload

    def overflow(*args):
        payload = build(*args)
        payload["decisionFacts"] = build_decision_facts({
            **payload, "rules": {"necessary": "长" * 9000}})
        return payload

    monkeypatch.setattr(runner, "_build_payload", overflow)
    await runner._run_decision(_BacktestJob("facts", runner), frame, {}, 60, [])
    record = runner.decider._journal[-1]
    assert record["proposal"]["action"] == action
    assert record["facts"]["factsUnavailable"] is True
    if action == "open_long":
        assert record["finalDecision"]["action"] == "hold"
        assert record["gateVeto"]["reason"] == "facts_unavailable"
        assert runner.main._pending == []
    else:
        assert record["finalDecision"]["action"] == "close"
        assert runner.main._pending[0].action == "close"


def test_backtest_quota_tracks_only_actual_open_enqueue_and_exception(monkeypatch):
    from app.ai_auto.signals import TradeDecision
    runner, _ = _integrity_backtest(monkeypatch)
    runner._round_contrarian = True
    decision = TradeDecision("open_long", .5, .9, "测试")
    original = runner.main.submit

    def broken(_):
        raise RuntimeError("模拟 broker 提交失败")

    monkeypatch.setattr(runner.main, "submit", broken)
    with pytest.raises(RuntimeError, match="模拟 broker"):
        runner._execute(decision)
    assert runner._entry_quota.reserved == 0
    assert runner._entry_quota.used == 0
    monkeypatch.setattr(runner.main, "submit", original)
    first = runner._execute(decision)
    assert first["executionReceipt"]["status"] == "accepted"
    assert runner._entry_quota.used == 1
    runner.main.fill_pending(100, runner.start_ts, 60)
    repeated = runner._execute(decision)
    assert repeated["ok"] is False
    assert repeated["executionReceipt"]["status"] == "not_submitted"
    assert repeated["executionReceipt"]["submissionPhase"] == "not_sent"
    assert runner._entry_quota.used == 1
    assert runner._entry_quota.reserved == 0


@pytest.mark.asyncio
async def test_backtest_record_uses_current_simulation_time_and_full_costs(monkeypatch):
    from app.ai_auto.backtest import _BacktestJob
    runner, frame = _integrity_backtest(monkeypatch)
    ts = int(frame.iloc[61]["ts"])
    await runner._run_decision(_BacktestJob("clock", runner), frame, {}, 61, [])
    record = runner.decider._journal[-1]
    facts = record["facts"]
    assert record["executionReceipt"]["observedAtTs"] == ts
    assert record["closedBarTs"] == ts
    assert facts["asOfTs"] == facts["economics"]["asOfTs"] == ts
    cost = facts["costSnapshot"]
    assert cost["asOfTs"] == ts
    assert cost["liquidityAssumption"] == "simulation"
    assert cost["feeSource"] == "simulation"
    assert cost["stale"] is False
    assert cost["spreadBps"] == runner.settings.default_slippage_bps
    assert cost["impactBps"] == 0
    assert "fundingBps" in cost
    assert facts["rules"]["risk"]["stop_loss_pct"] == runner._oco_risk.stop_loss_pct


def test_backtest_position_preserves_tiny_entry_and_epoch(monkeypatch):
    from app.ai_auto.signals import TradeDecision
    runner, _ = _integrity_backtest(monkeypatch)
    runner._execute(TradeDecision("open_long", .5, .9, "测试"))
    runner.main.fill_pending(1.234e-8, runner.start_ts, 60)
    position = runner._position_payload(1.234e-8)
    assert position["avgEntry"] == runner.main.pos.entry_price
    assert position["positionEpoch"] == runner.main.pos.entry_bar


@pytest.mark.parametrize("rate, side", [(.001, "long"), (-.001, "short")])
def test_backtest_flat_preview_covers_adverse_funding_side(monkeypatch, rate, side):
    from app.ai_auto.funding_ledger import BacktestFundingCurve
    from app.ai_auto.signals import compute_consensus

    runner, frame = _integrity_backtest(monkeypatch)
    ts = int(frame.iloc[61]["ts"])
    runner._curve = BacktestFundingCurve([
        {"fundingTs": ts - 28800, "fundingRate": rate},
        {"fundingTs": ts, "fundingRate": rate}])
    payload = runner._build_payload(
        1, ts, 100, frame, {}, 61, [], compute_consensus([]))
    cost = payload["costAwareness"]
    assert cost["previewSide"] == side
    assert cost["roundTripBps"] == runner._sizing_cost_bps(ts, f"open_{side}")


@pytest.mark.parametrize("side", ["long", "short"])
def test_backtest_position_cost_uses_paid_fee_and_current_funding(monkeypatch, side):
    from app.ai_auto.funding_ledger import BacktestFundingCurve
    from app.ai_auto.signals import TradeDecision, compute_consensus

    runner, frame = _integrity_backtest(monkeypatch)
    entry_ts, ts = int(frame.iloc[60]["ts"]), int(frame.iloc[61]["ts"])
    runner._curve = BacktestFundingCurve([
        {"fundingTs": ts - 57600, "realizedRate": .001},
        {"fundingTs": ts - 28800, "realizedRate": .001},
        {"fundingTs": ts, "realizedRate": .001}])
    runner._execute(TradeDecision(f"open_{side}", .5, .9, "测试"))
    runner.main.fill_pending(1.234e-8, entry_ts, 60)
    runner._track_ledgers(ts, 1.234e-8)
    payload = runner._build_payload(
        1, ts, 1.234e-8, frame, {}, 61, [], compute_consensus([]))
    px = payload["costAwareness"]["breakEvenPx"]
    assert px is not None
    pos = runner.main.pos
    sign = 1 if side == "long" else -1
    net = (sign * (px - pos.entry_price) * pos.qty - pos.entry_fee
           - px * pos.qty * runner.main.fee_rate
           - runner._funding.epoch.accrued_usd)
    assert net == pytest.approx(0, abs=1e-9)


@pytest.mark.parametrize("enqueue_open", [False, True])
def test_backtest_quota_settlement_survives_partial_submit_failure(monkeypatch,
                                                                 enqueue_open):
    from app.ai_auto.signals import TradeDecision
    from app.strategies.base import Signal
    runner, _ = _integrity_backtest(monkeypatch)
    runner._execute(TradeDecision("open_long", .5, .9, "测试"))
    runner.main.fill_pending(100, runner.start_ts, 60)
    runner._round_contrarian = True
    submit = runner.main.submit

    def partial(signals):
        submit(signals if enqueue_open else [Signal(side="close")])
        raise RuntimeError("入队之后异常")

    monkeypatch.setattr(runner.main, "submit", partial)
    with pytest.raises(RuntimeError, match="入队之后"):
        runner._execute(TradeDecision("open_short", .5, .9, "反手"))
    assert runner._entry_quota.reserved == 0
    assert runner._entry_quota.used == int(enqueue_open)


@pytest.mark.asyncio
async def test_backtest_round_freezes_funding_and_records_sizing_veto(monkeypatch):
    from dataclasses import replace
    from app.ai_auto.backtest import _BacktestJob
    from app.ai_auto.funding_ledger import BacktestFundingCurve
    runner, frame = _integrity_backtest(monkeypatch)
    ts = int(frame.iloc[61]["ts"])
    runner._curve = BacktestFundingCurve([
        {"fundingTs": ts - 28800, "fundingRate": .001},
        {"fundingTs": ts, "fundingRate": .001}])
    decide = runner.decider.decide
    captured = {}

    async def cache_changes(payload):
        captured.update(deepcopy(payload))
        result = await decide(payload)
        runner._curve = BacktestFundingCurve([])
        return result

    def sizing_veto(decision, context):
        assert context.cost_bps == captured["costAwareness"]["roundTripBps"]
        return (replace(decision, action="hold", size_fraction=None), None,
                {"floorVerdict": "solved_zero"})

    monkeypatch.setattr(runner.decider, "decide", cache_changes)
    monkeypatch.setattr(runner._sizing, "resolve", sizing_veto)
    await runner._run_decision(_BacktestJob("cost", runner), frame, {}, 61, [])
    record = runner.decider._journal[-1]
    assert record["proposal"]["action"] == "open_long"
    assert record["finalDecision"]["action"] == "hold"
    assert record["sizingVerdict"]["floorVerdict"] == "solved_zero"
    assert record["factsHash"] == captured["decisionFacts"]["factsHash"]
    assert record["facts"]["costSnapshot"]["fundingBps"] == 10
    assert runner.main._pending == []
    assert runner._entry_quota.used == runner._entry_quota.reserved == 0


def test_backtest_persists_cost_identity_and_reports_quota_refusal(monkeypatch):
    from app.ai_auto.signals import TradeDecision
    runner, _ = _integrity_backtest(monkeypatch)
    config = runner._run_config()
    assert config["costBasis"] == "net"
    assert config["accountingVersion"] == config["costModelVersion"] == 2
    runner._round_contrarian = True
    runner._entry_quota.max_per_session = 0
    result = runner._execute(TradeDecision("open_long", .5, .9, "测试"))
    assert result["ok"] is False
    receipt = result["executionReceipt"]
    assert receipt["status"] == "not_submitted"
    assert receipt["reason"] == "quota_exhausted"
    assert receipt["submissionPhase"] == "not_sent"
    assert receipt["instId"] == runner.inst_id
    assert receipt["clOrdId"]
    assert runner.main._pending == []


@pytest.mark.asyncio
async def test_backtest_low_confidence_does_not_invent_original_hold(monkeypatch):
    from app.ai_auto.backtest import _BacktestJob
    from .test_ai_backtest_runner import _StubLLM
    runner, frame = _integrity_backtest(monkeypatch)
    runner.settings.ai_trade_min_confidence = .5
    runner.decider._llm = _StubLLM(lambda _: {
        "action": "open_long", "sizeFraction": .5,
        "confidence": .01, "reason": "低置信度提议"})
    await runner._run_decision(_BacktestJob("low", runner), frame, {}, 61, [])
    record = runner.decider._journal[-1]
    assert record["finalDecision"]["action"] == "hold"
    assert record["proposal"] is None
    assert runner.main._pending == []
