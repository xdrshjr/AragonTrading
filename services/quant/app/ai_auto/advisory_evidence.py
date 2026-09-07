"""外部参考材料的冻结身份及有界摘要副本；不参与交易门禁。"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from .decision_memory import canonical_json

ADVISORY_SUMMARY_MAX_CHARS = 4096
ADVISORY_NAMES = ("intel", "experience", "signalPriors")
REFLECTION_NAMES = ("experienceCounter", "intelExpand")
_IDENTITY_FIELDS = ("runId", "instId", "bar", "round")
_SECTION_FIELDS = (
    "sourceHash", "renderedHash", "renderedChars", "enabled", "omitted",
)


def evidence_identity(payload: dict) -> dict:
    """返回 payload 的会话/轮次身份，旧调用可从 decisionFacts 补齐。"""
    facts = payload.get("decisionFacts") or {}
    return {key: payload.get(key, facts.get(key)) for key in _IDENTITY_FIELDS}


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def freeze_evidence(payload: dict, rendered: dict) -> dict:
    """冻结完整 wire 与渲染全文；输入为空文本仍与关闭来源区分。"""
    snapshot = {"evidenceVersion": 3, "identity": evidence_identity(payload),
                "wire": {}}
    names = (REFLECTION_NAMES if any(name in rendered
             for name in REFLECTION_NAMES) else ADVISORY_NAMES)
    for name in names:
        text = rendered.get(name, "")
        source = deepcopy(payload.get(name))
        enabled = source is not None and source is not False
        if isinstance(source, dict) and source.get("enabled") is False:
            enabled = False
        source = source if enabled else None
        text = text if enabled else ""
        snapshot["wire"][name] = source
        snapshot[name] = {
            "sourceHash": _hash_text(canonical_json(source)) if enabled
            else None,
            "renderedHash": _hash_text(text) if enabled else None,
            "renderedChars": len(text), "text": text,
            "enabled": enabled, "omitted": False,
        }
    return snapshot


def evidence_hashes(snapshot: dict) -> dict:
    """返回必要事实使用的三段哈希，不携带外部原文或 wire。"""
    return {name: {key: (snapshot.get(name) or {}).get(key)
                   for key in ("sourceHash", "renderedHash")}
            for name in ADVISORY_NAMES}


def unavailable_evidence(reason: str) -> dict:
    """返回固定有界的未发送信封；reason 由内部稳定原因码提供。"""
    return {"evidenceVersion": 3, "evidenceUnavailable": True,
            **{key: None for key in _IDENTITY_FIELDS},
            "sent": False, "reason": reason}


def _summary_metadata(snapshot: dict, names: tuple) -> dict:
    if snapshot.get("evidenceUnavailable"):
        return unavailable_evidence("evidence_unavailable")
    result = {"evidenceVersion": snapshot.get("evidenceVersion"),
              **deepcopy(snapshot.get("identity") or {}),
              "sent": snapshot.get("sent", True),
              "evidenceUnavailable": False}
    for name in names:
        section = snapshot.get(name)
        if not isinstance(section, dict):
            return unavailable_evidence("evidence_missing")
        result[name] = {key: deepcopy(section.get(key))
                        for key in _SECTION_FIELDS}
        result[name].update(text=None, omitted=bool(section.get("enabled")))
    return result


def _serialized_chars(envelope: dict) -> int:
    # 自身数字位也影响长度，迭代至固定点后才记录真实总包字符数。
    advisory = envelope["advisoryEvidence"]
    advisory.setdefault("serializedChars", 0)
    while True:
        count = len(json.dumps(envelope, ensure_ascii=False))
        if advisory["serializedChars"] == count:
            return count
        advisory["serializedChars"] = count


def _fits(envelope: dict) -> bool:
    return _serialized_chars(envelope) <= ADVISORY_SUMMARY_MAX_CHARS


def bound_summary_evidence(snapshot: dict,
                           reflection: dict | None = None) -> dict:
    """按情报、经验、先验、反思顺序整段回填，总 JSON 不超过 4096 字符。"""
    sources = [("advisoryEvidence", snapshot, ADVISORY_NAMES)]
    if reflection is not None:
        sources.append(("reflectionEvidence", reflection, REFLECTION_NAMES))
    envelope = {key: _summary_metadata(source, names)
                for key, source, names in sources}
    # RoundDecisionRecord 的稳定 wire 始终带此键，null 也占实际摘要预算。
    envelope.setdefault("reflectionEvidence", None)
    if not _fits(envelope):
        envelope = {"advisoryEvidence": unavailable_evidence(
            "metadata_budget_exceeded"), "reflectionEvidence": None}
        _serialized_chars(envelope)
        return envelope
    for key, source, names in sources:
        target = envelope[key]
        if target.get("evidenceUnavailable"):
            continue
        for name in names:
            section = target[name]
            if not section.get("enabled"):
                continue
            section.update(text=source[name].get("text"), omitted=False)
            if not _fits(envelope):
                section.update(text=None, omitted=True)
    _serialized_chars(envelope)
    return envelope


def has_same_evidence(left: dict, right: dict) -> bool:
    """比较来源/渲染身份与范围，不将轮号和摘要开销当成新市场证据。"""
    for key, names in (("advisoryEvidence", ADVISORY_NAMES),
                       ("reflectionEvidence", REFLECTION_NAMES)):
        before, after = left.get(key), right.get(key)
        if before is None or after is None:
            if before != after:
                return False
            continue
        if any(not before.get(field) or before.get(field) != after.get(field)
               for field in ("runId", "instId", "bar")):
            return False
        for name in names:
            if any(before[name].get(field) != after[name].get(field)
                   for field in ("enabled", "sourceHash", "renderedHash")):
                return False
    return True


def has_complete_evidence(entry: dict) -> bool:
    """仅完整且已发送的 v3 材料可形成可折叠或非 legacy 摘要。"""
    if entry.get("evidenceVersion") != 3 or entry.get("legacy"):
        return False
    advisory = entry.get("advisoryEvidence")
    if not _is_complete(advisory, ADVISORY_NAMES):
        return False
    reflection = entry.get("reflectionEvidence")
    if reflection is not None:
        return _is_complete(reflection, REFLECTION_NAMES)
    return not entry.get("reflection")


def _is_complete(evidence: dict | None, names: tuple) -> bool:
    if not evidence or evidence.get("evidenceUnavailable"):
        return False
    if evidence.get("evidenceVersion") != 3 or not evidence.get("sent"):
        return False
    for name in names:
        section = evidence.get(name)
        if not isinstance(section, dict) or section.get("omitted"):
            return False
        if section.get("enabled") and any(section.get(key) is None
                for key in ("sourceHash", "renderedHash", "text")):
            return False
    return True


def attach_snapshot(payload: dict, snapshot: dict, facts: dict | None) -> dict:
    """同轮使用冻结的基础材料和事实，同时保留反思新增材料。"""
    result = {**payload, **deepcopy(snapshot["wire"]),
              "advisorySnapshot": deepcopy(snapshot)}
    if facts is not None:
        result["decisionFacts"] = deepcopy(facts)
    return result
