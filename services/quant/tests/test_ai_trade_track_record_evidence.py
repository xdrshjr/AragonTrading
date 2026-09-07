"""四段冻结证据的发送、恢复及真实 JSON 预算回归。"""
from copy import deepcopy
import json

import pytest

from app.agent.llm.base import StreamEvent
from app.ai_auto.advisory_evidence import (
    ADVISORY_NAMES, bound_summary_evidence, freeze_evidence,
    has_complete_evidence,
)
from app.ai_auto.decision_memory import build_decision_facts
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.prompts import build_advisory_evidence
from app.ai_auto.track_record import build_track_record
from app.config import Settings
from .test_ai_trade_track_record import _history, _settings
from .test_ai_trade_memory_integrity import entry


def payload():
    return {"runId": 71, "instId": "BTC-USDT-SWAP", "bar": "1m",
            "round": 1, "lastPx": 100, "position": None,
            "trackRecord": build_track_record(_history(), [], _settings())}


def test_track_record_is_first_frozen_section_and_changes_facts():
    first = payload()
    assert ADVISORY_NAMES == (
        "trackRecord", "intel", "experience", "signalPriors")
    frozen = build_advisory_evidence(first)
    assert frozen["evidenceVersion"] == 4
    assert frozen["wire"]["trackRecord"] == first["trackRecord"]
    facts = build_decision_facts(first)
    assert facts["schemaVersion"] == 2
    assert "trackRecord" in facts["evidenceHashes"]
    for key, value in (("expectancyUsd", 123), ("snapshotId", "new"),
                       ("coverage", {"totalTrips": 999})):
        changed = deepcopy(first)
        changed["trackRecord"]["history"][key] = value
        assert build_decision_facts(changed)["evidenceFingerprint"] != (
            facts["evidenceFingerprint"])


def test_four_sections_obey_serialized_json_budget_and_keep_hashes():
    raw = {**payload(), "intel": {"enabled": True},
           "experience": {"enabled": True}, "signalPriors": {"n": 1}}
    frozen = freeze_evidence(raw, {
        "trackRecord": "战绩短句", "intel": '\n"' * 4000,
        "experience": '\n"' * 4000, "signalPriors": '\n"' * 4000})
    envelope = bound_summary_evidence(frozen)
    evidence = envelope["advisoryEvidence"]
    assert len(json.dumps(envelope, ensure_ascii=False)) <= 4096
    assert evidence["serializedChars"] == len(
        json.dumps(envelope, ensure_ascii=False))
    assert evidence["trackRecord"]["text"] == "战绩短句"
    assert evidence["intel"]["omitted"] is True
    assert evidence["intel"]["sourceHash"]
    assert evidence["intel"]["renderedChars"] == 8000
    assert not has_complete_evidence({"evidenceVersion": 4, **envelope})


@pytest.mark.asyncio
async def test_actual_retry_reflection_commit_keep_frozen_track_record():
    current = payload()
    current["decisionFacts"] = build_decision_facts(current)
    frozen_track = deepcopy(current["trackRecord"])

    class MutatingLLM:
        def __init__(self):
            self.texts = []

        async def stream(self, *, system, messages, tools):
            self.texts.append(messages[-1].content[0].text)
            current["trackRecord"]["history"]["snapshotId"] = "MUTATED"
            current["trackRecord"]["history"]["roundTrips"] = 987654
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
    decision, _ = await decider.decide(current)
    await decider.reflect(current, decision)
    await decider.commit_round(current, decision, {"ok": True, "traded": False})
    assert len(llm.texts) == 3
    for text in llm.texts:
        assert "历史 193 笔往返" in text
        assert "987654" not in text
        assert "历史交易净额，不含资金费" in text
    frozen = decider.frozen_round_payload(current)
    assert frozen["trackRecord"] == frozen_track
    section = decider._journal[-1]["advisoryEvidence"]["trackRecord"]
    assert "历史 193 笔往返" in section["text"]
    current["round"] = 2
    await decider.decide(current)
    assert "历史 987654 笔往返" in llm.texts[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize("old_version", [2, 3, 4, 99])
async def test_summary_version_preserves_legacy_input(old_version):
    from .test_ai_trade_decider import _decider
    decider = _decider([[StreamEvent("text_delta", text="保留旧叙述的新摘要")]])
    decider.seed_from_snapshot(summary="旧叙述", last_round=9,
        facts_version=2, evidence_version=old_version)
    decider._journal = [entry(10)]
    assert await decider.summarize() == "保留旧叙述的新摘要"
    wire = decider.memory_wire()
    assert wire["legacyNarrative"] is (old_version != 4)
    assert wire["summaryEvidenceVersion"] == (4 if old_version == 4 else None)
    assert wire["evidenceVersion"] == 4


def test_unrecognized_snapshot_never_becomes_complete_current_evidence():
    frozen = build_advisory_evidence(payload())
    frozen["evidenceVersion"] = 99
    envelope = bound_summary_evidence(frozen)
    assert not has_complete_evidence({"evidenceVersion": 4, **envelope})


@pytest.mark.parametrize("version, enabled", [(99, True), (4, False)])
def test_future_or_disabled_snapshot_text_is_not_rendered(version, enabled):
    from app.ai_auto.prompts import build_decision_prompt
    current = payload()
    snapshot = build_advisory_evidence(current)
    snapshot["evidenceVersion"] = version
    snapshot["trackRecord"].update(text="UNREADABLE", enabled=enabled)
    current["advisorySnapshot"] = snapshot
    assert "UNREADABLE" not in build_decision_prompt(current)


@pytest.mark.asyncio
@pytest.mark.parametrize("summary_version", [None, 3, 4, 99])
async def test_resume_reads_saved_summary_version_not_runtime_capability(
        monkeypatch, summary_version):
    from types import SimpleNamespace
    from app.ai_auto.resume import _restore_summary
    from .test_ai_trade_decider import _decider

    async def read_snapshot(run_id):
        assert run_id == 71
        return {"roundsCompleted": 12, "finalSummary": "恢复前叙述",
                "aiStats": {"integrity": {"memory": {
                    "factsVersion": 2, "evidenceVersion": 4,
                    "summaryEvidenceVersion": summary_version,
                    "summaryAsOfTs": 123, "legacyNarrative": False,
                }}}}

    monkeypatch.setattr("app.ai_auto.resume._run_snapshot", read_snapshot)
    session = SimpleNamespace(decider=_decider([]))
    await _restore_summary(session, 71)
    wire = session.decider.memory_wire()
    assert session.decider.summary == "恢复前叙述"
    assert session.rounds_completed == 12
    assert wire["summaryAsOfTs"] == 123
    assert wire["summaryEvidenceVersion"] == (
        4 if summary_version == 4 else None)
    assert wire["legacyNarrative"] is (summary_version != 4)
