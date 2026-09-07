"""外部证据冻结、实际 JSON 预算和摘要来源版本的行为验收。"""
from copy import deepcopy
import asyncio
import hashlib
import json

import pytest

from app.ai_auto import prompts
from app.ai_auto.decision_memory import (
    EVIDENCE_VERSION, RoundDecisionRecord, build_decision_facts, canonical_json,
)


def _decider(scripts):
    from .test_ai_trade_decider import _decider as create
    return create(scripts)


def _tool_events(args):
    from .test_ai_trade_decider import _tool_events as create
    return create(args)


def _snapshot_payload(round_no=1):
    payload = {"round": round_no, "lastPx": 100, "position": None,
               "runId": 71,
               "instId": "BTC-USDT-SWAP", "bar": "1m",
               "intel": {"mode": "full", "lines": ["情报初稿"]},
               "experience": {"l0": []},
               "signalPriors": {"ageS": 60, "long": {"p": .6}}}
    payload["advisorySnapshot"] = prompts.build_advisory_evidence(payload)
    from app.ai_auto.advisory_evidence import evidence_hashes
    payload["evidenceHashes"] = evidence_hashes(payload["advisorySnapshot"])
    payload["decisionFacts"] = build_decision_facts(payload)
    return payload


def _commit_record(payload):
    return RoundDecisionRecord({"action": "hold"}, {"action": "hold"},
                               payload["decisionFacts"])


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_snapshot_distinguishes_disabled_from_enabled_empty_rendering():
    assert callable(getattr(prompts, "build_advisory_evidence", None))
    snapshot = prompts.build_advisory_evidence({"experience": {"l0": []}})
    assert snapshot["evidenceVersion"] == EVIDENCE_VERSION
    assert snapshot["intel"]["enabled"] is False
    assert snapshot["intel"]["sourceHash"] is None
    assert snapshot["experience"]["enabled"] is True
    assert snapshot["experience"]["text"] == ""
    assert snapshot["experience"]["renderedHash"] == _hash("")
    assert snapshot["experience"]["sourceHash"] == _hash(
        canonical_json({"l0": []}))


def test_snapshot_matches_renderers_and_freezes_full_wire(monkeypatch):
    for name in ("intel", "experience", "signal_priors"):
        monkeypatch.setattr(prompts, f"_format_{name}",
                            lambda wire: str(wire or ""))
    payload = _snapshot_payload()
    frozen = payload["advisorySnapshot"]
    original = deepcopy(frozen)
    payload["intel"]["lines"][0] = "事后篡改"
    text = prompts.build_decision_prompt(payload)
    assert "事后篡改" not in text
    assert frozen == original
    for key in ("intel", "experience", "signalPriors"):
        section = frozen[key]
        assert section["renderedHash"] == _hash(section["text"])
        assert section["renderedChars"] == len(section["text"])
        assert payload["decisionFacts"]["evidenceHashes"][key] == {
            "sourceHash": section["sourceHash"],
            "renderedHash": section["renderedHash"],
        }
    assert payload["decisionFacts"]["evidenceVersion"] == EVIDENCE_VERSION
    assert "advisorySnapshot" not in payload["decisionFacts"]


@pytest.mark.parametrize("text", ['"\\\n中😀' * 1500, "长" * 4096],
                         ids=["escaped_unicode", "long_text"])
def test_summary_budget_omits_whole_sections_and_preserves_hashes(text):
    from app.ai_auto.advisory_evidence import (
        ADVISORY_SUMMARY_MAX_CHARS, bound_summary_evidence,
        freeze_evidence,
    )
    snapshot = freeze_evidence({"intel": {"enabled": True}},
                               {"intel": text})
    envelope = bound_summary_evidence(snapshot)
    assert len(json.dumps(envelope, ensure_ascii=False)) <= (
        ADVISORY_SUMMARY_MAX_CHARS)
    section = envelope["advisoryEvidence"]["intel"]
    assert section["text"] is None
    assert section["omitted"] is True
    assert section["renderedHash"] == _hash(text)
    assert section["renderedChars"] == len(text)
    assert "证据不足" in prompts.build_summary_prompt(None, [envelope])


def test_budget_counts_exact_json_boundary_and_metadata_overflow():
    from app.ai_auto.advisory_evidence import (
        bound_summary_evidence, freeze_evidence,
    )
    for size in range(1, 4097):
        snapshot = freeze_evidence({"intel": {}}, {"intel": "中" * size})
        envelope = bound_summary_evidence(snapshot)
        if len(json.dumps(envelope, ensure_ascii=False)) == 4096:
            break
    else:
        pytest.fail("应能保留恰好 4096 字符的完整 JSON 信封")
    assert envelope["advisoryEvidence"]["intel"]["text"] == "中" * size
    snapshot["intel"]["sourceHash"] = "无界错误元数据" * 2000
    overflow = bound_summary_evidence(snapshot)
    assert len(json.dumps(overflow, ensure_ascii=False)) <= 4096
    assert overflow["advisoryEvidence"]["evidenceUnavailable"] is True


@pytest.mark.asyncio
async def test_decide_mutation_retry_reflection_and_commit_share_snapshot():
    from app.agent.llm.base import StreamEvent
    from app.ai_auto.decider import AiTradeDecider
    from app.config import Settings
    payload = _snapshot_payload()
    original = deepcopy(payload)

    class MutatingLLM:
        texts = []

        async def stream(self, *, system, messages, tools):
            self.texts.append(messages[-1].content[0].text)
            payload["intel"]["lines"][0] = "事后篡改"
            payload["advisorySnapshot"]["intel"]["text"] = "事后篡改"
            payload.get("intelExpand", {})["rendered"] = "事后扩展"
            if len(self.texts) == 1:
                yield StreamEvent("message_done", stop_reason="end")
                return
            args = ({"verdict": "confirm", "reason": "通过"}
                    if "reflection" in tools[0].name else
                    {"action": "hold", "confidence": .9, "reason": "等候"})
            yield StreamEvent("tool_use_start", id="t", name=tools[0].name)
            yield StreamEvent("tool_use_args_delta", id="t",
                              partial_json=json.dumps(args))
            yield StreamEvent("message_done", stop_reason="tool_use")

    llm = MutatingLLM()
    decider = AiTradeDecider(llm, "sys", context_refresh_rounds=20,
        reflection_enabled=True, settings=Settings(
            ai_trade_decision_max_retries=1,
            ai_trade_decision_retry_backoff_base_s=0))
    decision, _ = await decider.decide(payload)
    payload["intelExpand"] = {"rendered": "独立反思扩展"}
    await decider.reflect(payload, decision)
    await decider.commit_round(payload, decision, {"ok": True},
                               record=_commit_record(original))
    assert len(llm.texts) == 3
    assert all("事后篡改" not in text for text in llm.texts)
    assert "事后篡改" not in decider._context[-3].content[0].text
    assert "独立反思扩展" in llm.texts[-1]
    entry = decider._journal[-1]
    assert entry["factsHash"] == original["decisionFacts"]["factsHash"]
    assert entry["advisoryEvidence"]["intel"]["text"] == (
        original["advisorySnapshot"]["intel"]["text"])
    assert entry["reflectionEvidence"]["intelExpand"]["text"] == (
        "独立反思扩展")
    assert entry["reflectionEvidence"]["sent"] is True
    assert entry["evidenceVersion"] == EVIDENCE_VERSION


@pytest.mark.asyncio
async def test_commit_unsent_or_different_round_never_reuses_prior_snapshot():
    decider = _decider([_tool_events({"action": "hold"})])
    first = _snapshot_payload()
    decision, _ = await decider.decide(first)
    for payload in (_snapshot_payload(2), {**first, "runId": 72}):
        await decider.commit_round(payload, decision, {"ok": True},
                                   record=_commit_record(payload))
        evidence = decider._journal[-1]["advisoryEvidence"]
        assert evidence["evidenceUnavailable"] is True
        assert evidence["sent"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, 2, 3, 99])
async def test_old_summary_cannot_upgrade_by_new_capability_or_resummarize(version):
    from app.agent.llm.base import StreamEvent
    decider = _decider([[StreamEvent("text_delta", text="重新摘要"),
                         StreamEvent("message_done")]])
    decider.seed_from_snapshot(summary="旧错误结论", last_round=1,
        facts_version=2, evidence_version=version)
    memory = decider.memory_wire()
    assert memory["evidenceVersion"] == EVIDENCE_VERSION
    assert memory["summaryEvidenceVersion"] is None
    assert memory["legacyNarrative"] is True
    assert await decider.summarize() == "重新摘要"
    assert decider.memory_wire()["summaryEvidenceVersion"] is None
    assert decider.memory_wire()["legacyNarrative"] is True


@pytest.mark.asyncio
async def test_summary_failure_and_cancel_preserve_all_provenance_fields():
    decider = _decider([])
    decider.seed_from_snapshot(summary="完整证据摘要", last_round=4,
        facts_version=2, evidence_version=EVIDENCE_VERSION, summary_as_of_ts=12)
    before = decider.memory_wire()
    assert await decider.summarize() is None
    assert decider.memory_wire() == before

    async def cancel(*args):
        raise asyncio.CancelledError()

    decider._stream_summary = cancel
    with pytest.raises(asyncio.CancelledError):
        await decider.summarize()
    assert decider.memory_wire() == before
    assert decider.summary == "完整证据摘要"


@pytest.mark.parametrize("source, patch", [
    ("intel", {"status": "expired", "ageS": 999}),
    ("experience", {"sampleN": 30, "updatedTs": 50}),
    ("signalPriors", {"status": "stale"}),
])
def test_same_rendered_text_retains_changed_source_status(source, patch):
    payload = _snapshot_payload()
    first = payload["advisorySnapshot"][source]
    payload[source].update(patch)
    changed = prompts.build_advisory_evidence(payload)[source]
    assert first["text"] == changed["text"]
    assert first["renderedHash"] == changed["renderedHash"]
    assert first["sourceHash"] != changed["sourceHash"]


@pytest.mark.asyncio
@pytest.mark.parametrize("omitted", [False, True])
async def test_successful_summary_tracks_actual_material_completeness(omitted):
    from app.agent.llm.base import StreamEvent
    decider = _decider([_tool_events({"action": "hold"}),
        [StreamEvent("text_delta", text="新摘要"), StreamEvent("message_done")]])
    payload = _snapshot_payload()
    if omitted:
        payload["intel"]["lines"] = ["证据很长" * 2000]
        payload["advisorySnapshot"] = prompts.build_advisory_evidence(payload)
        from app.ai_auto.advisory_evidence import evidence_hashes
        payload["evidenceHashes"] = evidence_hashes(payload["advisorySnapshot"])
        payload["decisionFacts"] = build_decision_facts(payload)
    decision, _ = await decider.decide(payload)
    await decider.commit_round(payload, decision, {"ok": True},
                               record=_commit_record(payload))
    assert await decider.summarize() == "新摘要"
    memory = decider.memory_wire()
    assert memory["legacyNarrative"] is omitted
    assert memory["summaryEvidenceVersion"] == (
        None if omitted else EVIDENCE_VERSION)
    assert memory["summaryAsOfTs"] is not None


def test_legacy_flag_survives_current_version_and_empty_summary_has_no_version():
    decider = _decider([])
    assert decider.memory_wire()["summaryEvidenceVersion"] is None
    decider.seed_from_snapshot(summary="混合旧证据", last_round=4,
        facts_version=2, evidence_version=EVIDENCE_VERSION,
        legacy_narrative=True)
    assert decider.memory_wire()["summaryEvidenceVersion"] is None
    assert decider.memory_wire()["legacyNarrative"] is True


@pytest.mark.asyncio
async def test_cancelled_retry_cannot_commit_evidence_as_sent(monkeypatch):
    from app.agent.llm.base import StreamEvent
    from app.config import Settings
    from .test_ai_trade_decider import _decider as create
    decider = create([[StreamEvent("message_done")]], settings=Settings(
        ai_trade_decision_max_retries=1))
    payload = _snapshot_payload()

    async def cancel(_):
        raise asyncio.CancelledError()

    monkeypatch.setattr(decider, "_retry_backoff", cancel)
    with pytest.raises(asyncio.CancelledError):
        await decider.decide(payload)
    from app.ai_auto.decider import TradeDecision
    await decider.commit_round(payload, TradeDecision("hold", None, None, "取消"),
        {"ok": True}, record=_commit_record(payload))
    evidence = decider._journal[-1]["advisoryEvidence"]
    assert evidence["evidenceUnavailable"] is True
    assert evidence["sent"] is False


@pytest.mark.asyncio
async def test_unadopted_reask_keeps_base_evidence_and_actual_request_hash():
    from app.agent.llm.base import StreamEvent
    from app.ai_auto.reasoning_protocol import profile_by_name
    decider = _decider([
        [StreamEvent("text_delta", text="初次分析"),
         *_tool_events({"action": "hold"})],
        [StreamEvent("message_done")],
    ])
    decider._depth = profile_by_name("standard")
    payload = _snapshot_payload()
    decision, degrade = await decider.decide(payload)
    assert decision.action == "hold" and degrade is None
    assert decider.last_reasoning.reask_adopted is False
    # 从模型端口读取实际请求，避免依赖尚未提交的日志产品出口。
    actual = decider._llm.last_messages[-1].content[0].text
    base = prompts.build_decision_prompt(payload)
    assert actual.startswith(base)
    assert len(actual) > len(base)
    await decider.commit_round(payload, decision, {"ok": True},
                               record=_commit_record(payload))
    assert decider._context[-3].content[0].text == (
        prompts.build_decision_prompt(payload))
    assert decider._llm.calls == 2


def test_round_record_deep_copies_advisory_and_reflection_evidence():
    from app.ai_auto.advisory_evidence import bound_summary_evidence
    payload = _snapshot_payload()
    envelope = bound_summary_evidence(payload["advisorySnapshot"])
    record = RoundDecisionRecord(None, {"action": "hold"},
        payload["decisionFacts"],
        advisory_evidence=envelope["advisoryEvidence"])
    envelope["advisoryEvidence"]["intel"]["text"] = "调用方改写"
    first = record.to_wire()
    assert first["advisoryEvidence"]["intel"]["text"] != "调用方改写"
    first["advisoryEvidence"]["intel"]["text"] = "返回值改写"
    assert record.to_wire()["advisoryEvidence"]["intel"]["text"] != (
        "返回值改写")


@pytest.mark.asyncio
async def test_journal_payload_freezes_reflection_raw_and_actual_request():
    from app.agent.llm.base import StreamEvent
    decider = _decider([
        _tool_events({"action": "open_long", "sizeFraction": .2,
                      "confidence": .9}),
        [StreamEvent("tool_use_start", id="r", name="submit_trade_reflection"),
         StreamEvent("tool_use_args_delta", id="r",
                     partial_json='{"verdict":"confirm"}'),
         StreamEvent("message_done")],
    ])
    decider.reflection_enabled = True
    payload = _snapshot_payload()
    decision, _ = await decider.decide(payload)
    payload["experienceCounter"] = {"rendered": "本轮反例", "sampleN": 2}
    payload["intelExpand"] = {"rendered": "本轮展开", "ids": [71]}
    await decider.reflect(payload, decision)
    actual = decider._llm.last_messages[-1].content[0].text
    payload["experienceCounter"]["rendered"] = "事后反例"
    payload["intelExpand"]["ids"].append(72)
    payload["advisorySnapshot"]["intel"]["text"] = "事后情报"
    frozen = decider.frozen_round_payload(payload)
    assert frozen["experienceCounter"]["rendered"] == "本轮反例"
    assert frozen["intelExpand"]["ids"] == [71]
    request = frozen["reflectionRequest"]
    assert request["finalUserText"] == actual
    assert request["finalUserSha256"] == _hash(actual)
    assert prompts.build_reflection_prompt(frozen, decision) == actual
    frozen["intelExpand"]["ids"].append(99)
    assert decider.frozen_round_payload(payload)["intelExpand"]["ids"] == [71]


def test_final_facts_fingerprint_reads_the_same_frozen_wire_as_hashes():
    payload = _snapshot_payload()
    first = build_decision_facts(payload)
    payload["intel"]["lines"] = ["冻结后发生变化"]
    second = build_decision_facts(payload)
    assert first["evidenceHashes"] == second["evidenceHashes"]
    assert first["evidenceFingerprint"] == second["evidenceFingerprint"]


@pytest.mark.parametrize("with_reflection", [False, True])
def test_summary_keeps_scope_and_exact_total_serialized_characters(
        with_reflection):
    from app.ai_auto.advisory_evidence import bound_summary_evidence
    payload = _snapshot_payload(17)
    reflection = (prompts.build_reflection_evidence({**payload,
        "intelExpand": {"rendered": '反思"\\\n😀'}})
        if with_reflection else None)
    envelope = bound_summary_evidence(payload["advisorySnapshot"], reflection)
    advisory = envelope["advisoryEvidence"]
    assert {key: advisory.get(key) for key in (
        "runId", "instId", "round", "bar")} == {
            key: payload[key] for key in ("runId", "instId", "round", "bar")}
    assert advisory.get("serializedChars") == len(
        json.dumps(envelope, ensure_ascii=False))
    assert advisory["serializedChars"] <= 4096
    if reflection is not None:
        assert envelope["reflectionEvidence"]["round"] == 17


def test_evidence_scope_folding_ignores_round_and_budget_counter():
    from app.ai_auto.advisory_evidence import bound_summary_evidence
    from app.ai_auto.prompts import compact_journal_for_summary
    entries = []
    for number in (9, 10):
        payload = _snapshot_payload(number)
        envelope = bound_summary_evidence(payload["advisorySnapshot"])
        entries.append({**_commit_record(payload).to_wire(), **envelope,
                        "trigger": "interval", "executed": {"ok": True}})
    assert len(compact_journal_for_summary(entries)) == 1
    entries[-1]["advisoryEvidence"]["runId"] = 99
    assert len(compact_journal_for_summary(entries)) == 2


def _disconnected_client():
    from contextlib import asynccontextmanager
    import httpx
    from app.agent.llm.anthropic_client import AnthropicClient
    from app.agent.llm.retry import RetryConfig

    class DisconnectedClient(AnthropicClient):
        @asynccontextmanager
        async def _request_cm(self, url, body):
            raise httpx.ConnectError("离线连接失败，未发送请求")
            yield

    return DisconnectedClient(base_url="https://offline.invalid",
        api_key="unused", model="offline", anthropic_version="unused",
        user_agent="test", max_tokens=100, temperature=0, top_p=1,
        timeout=1, proxy=None, retry=RetryConfig(max_retries=0))


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["decision", "reflection"])
async def test_connection_failure_cannot_certify_sent_evidence(stage):
    from app.agent.llm.base import StreamEvent
    from app.ai_auto.advisory_evidence import has_complete_evidence
    decider = _decider([_tool_events({"action": "hold"}),
        [StreamEvent("text_delta", text="失败轮摘要"),
         StreamEvent("message_done")]])
    payload = _snapshot_payload()
    if stage == "decision":
        decider._llm = _disconnected_client()
    decision, reason = await decider.decide(payload)
    if stage == "reflection":
        decider.reflection_enabled = True
        decider.settings.ai_trade_reflection_max_retries = 0
        decider._reflection_llm = _disconnected_client()
        verdict = await decider.reflect(payload, decision)
        assert verdict.degrade_reason == "reflection_llm_error"
    else:
        assert reason == "llm_error"
    await decider.commit_round(payload, decision, {"ok": True},
                               record=_commit_record(payload))
    entry = decider._journal[-1]
    key = "advisoryEvidence" if stage == "decision" else "reflectionEvidence"
    assert entry[key]["sent"] is False
    assert entry[key]["evidenceUnavailable"] is True
    assert has_complete_evidence(entry) is False
    decider._summary_llm = _decider([
        [StreamEvent("text_delta", text="失败轮摘要"),
         StreamEvent("message_done")]])._llm
    assert await decider.summarize() == "失败轮摘要"
    assert decider.memory_wire()["summaryEvidenceVersion"] is None
    assert decider.memory_wire()["legacyNarrative"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["decision", "reflection"])
async def test_empty_stream_cannot_certify_sent_evidence(stage):
    decider = _decider([[], _tool_events({"action": "hold"})]
                        if stage == "decision" else
                        [_tool_events({"action": "hold"}), []])
    payload = _snapshot_payload()
    decision, _ = await decider.decide(payload)
    if stage == "reflection":
        decider.reflection_enabled = True
        decider.settings.ai_trade_reflection_max_retries = 0
        await decider.reflect(payload, decision)
    await decider.commit_round(payload, decision, {"ok": True},
                               record=_commit_record(payload))
    key = "advisoryEvidence" if stage == "decision" else "reflectionEvidence"
    assert decider._journal[-1][key]["sent"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["decision", "reflection"])
async def test_received_response_survives_later_retry_transport_failure(stage):
    from app.agent.llm.base import StreamEvent
    response = [StreamEvent("text_delta", text="已收到本轮材料"),
                StreamEvent("message_done")]
    failure = [StreamEvent("error", code="LLM_NETWORK_ERROR")]
    decider = _decider([response, failure] if stage == "decision" else
        [_tool_events({"action": "hold"}), response, failure])
    decider.settings.ai_trade_decision_max_retries = int(stage == "decision")
    decider.settings.ai_trade_retry_base_ms = 0
    decider.settings.ai_trade_reflection_max_retries = 1
    payload = _snapshot_payload()
    decision, _ = await decider.decide(payload)
    if stage == "reflection":
        decider.reflection_enabled = True
        await decider.reflect(payload, decision)
    await decider.commit_round(payload, decision, {"ok": True},
                               record=_commit_record(payload))
    key = "advisoryEvidence" if stage == "decision" else "reflectionEvidence"
    assert decider._journal[-1][key]["sent"] is True


@pytest.mark.asyncio
async def test_cancelled_reflection_retry_cannot_certify_evidence(monkeypatch):
    from app.agent.llm.base import StreamEvent
    decider = _decider([_tool_events({"action": "hold"}),
                        [StreamEvent("message_done")]])
    decider.reflection_enabled = True
    decider.settings.ai_trade_reflection_max_retries = 1
    payload = _snapshot_payload()
    decision, _ = await decider.decide(payload)

    async def cancel(_):
        raise asyncio.CancelledError()

    monkeypatch.setattr(decider, "_retry_backoff", cancel)
    with pytest.raises(asyncio.CancelledError):
        await decider.reflect(payload, decision)
    await decider.commit_round(payload, decision, {"ok": True},
                               record=_commit_record(payload))
    entry = decider._journal[-1]
    assert entry["advisoryEvidence"]["sent"] is True
    assert entry["reflectionEvidence"]["sent"] is False


@pytest.mark.asyncio
async def test_actual_summary_budget_includes_null_reflection_field():
    from app.ai_auto.advisory_evidence import bound_summary_evidence
    payload = _snapshot_payload()
    for size in range(2000, 4097):
        payload["intel"]["lines"] = ["x" * size]
        snapshot = prompts.build_advisory_evidence(payload)
        envelope = bound_summary_evidence(snapshot)
        if len(json.dumps(envelope, ensure_ascii=False)) == 4096:
            break
    else:
        pytest.fail("应能构造完整 JSON 信封恰好占用 4096 字符的输入")
    payload["advisorySnapshot"] = snapshot
    from app.ai_auto.advisory_evidence import evidence_hashes
    payload["evidenceHashes"] = evidence_hashes(snapshot)
    payload["decisionFacts"] = build_decision_facts(payload)
    decider = _decider([_tool_events({"action": "hold"})])
    decision, _ = await decider.decide(payload)
    await decider.commit_round(payload, decision, {"ok": True},
                               record=_commit_record(payload))
    prompt = prompts.build_summary_prompt(None, decider._journal)
    entries = json.loads(prompt.split("逐轮执行流水（JSON）：\n", 1)[1])
    actual = {key: entries[0][key] for key in (
        "advisoryEvidence", "reflectionEvidence")}
    assert actual["reflectionEvidence"] is None
    actual_chars = len(json.dumps(actual, ensure_ascii=False))
    assert actual_chars <= 4096
    assert actual["advisoryEvidence"]["serializedChars"] == actual_chars
