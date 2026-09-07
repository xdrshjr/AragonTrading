"""决策事实、证据身份和仅供摘要器使用的保守折叠规则。"""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import dataclass

FACTS_MAX_CHARS = 8192
EVIDENCE_VERSION = 4
_FACT_FIELDS = (
    "runId", "instId", "bar", "round", "asOfTs", "position",
    "restingOrder", "pendingOpen", "rules", "entryQuota", "funnel",
    "economics", "costSnapshot", "settlementValidation", "evidenceHashes",
)
_EVIDENCE_FIELDS = (
    "runId", "instId", "bar", "barTs", "closedBarTs", "lastPx", "marketDigest",
    "multiTimeframeContext", "tacticalContext", "tacticalTier",
    "signalBoard", "consensus", "baseRules", "trendVerdict", "trend",
    "position", "restingOrder", "pendingOpen", "rules", "entryQuota",
    "costSnapshot", "marketContext", "contextDisclosure",
)
_VOLATILE_FIELDS = frozenset((
    "round", "asOfTs", "as_of_ts", "observedAtTs", "updatedTs",
    "ageS", "ageSeconds", "ageMs", "elapsedMs", "latencyMs", "traceId",
    "fetchedAtTs", "expiresInS", "waitedS", "requestId",
))


def _finite(value):
    if isinstance(value, dict):
        return {str(key): _finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def canonical_json(value) -> str:
    """返回有限数字、显式 null 和排序键组成的完整规范 JSON。"""
    return json.dumps(_finite(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _hash(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _evidence_value(value):
    if isinstance(value, dict):
        return {key: _evidence_value(item) for key, item in value.items()
                if key not in _VOLATILE_FIELDS}
    if isinstance(value, (list, tuple)):
        return [_evidence_value(item) for item in value]
    return value


def build_evidence_fingerprint(payload: dict) -> str:
    """行情忽略墙钟噪声；外部材料完整 wire 保留时效与来源状态。"""
    selected = {key: payload.get(key) for key in _EVIDENCE_FIELDS}
    selected = _evidence_value(selected)
    wire = (payload.get("advisorySnapshot") or {}).get("wire", payload)
    from .advisory_evidence import ADVISORY_NAMES
    for key in ADVISORY_NAMES:
        selected[key] = wire.get(key)
    return _hash(selected)


def build_decision_facts(inputs: dict) -> dict:
    """冻结当前会话事实并计算 hash；必要事实超限时显式不可用。"""
    facts = _finite({key: inputs.get(key) for key in _FACT_FIELDS})
    if facts["evidenceHashes"] is None:
        from .advisory_evidence import evidence_hashes
        from .prompts import build_advisory_evidence
        snapshot = inputs.get("advisorySnapshot")
        if snapshot is None:
            snapshot = build_advisory_evidence(inputs)
        facts["evidenceHashes"] = evidence_hashes(snapshot)
    facts.update(schemaVersion=2, evidenceVersion=EVIDENCE_VERSION,
                 priceUnit="USDT")
    facts["evidenceFingerprint"] = build_evidence_fingerprint(inputs)
    # 可选解释不能挤掉真实持仓、pending、规则或费用来源。
    for key in ("diagnosis", "narrative"):
        if key in inputs:
            facts[key] = _finite(inputs[key])
    if len(canonical_json(facts)) > FACTS_MAX_CHARS - 80:
        facts.pop("narrative", None)
        diagnosis = facts.get("diagnosis")
        if isinstance(diagnosis, dict):
            diagnosis.pop("details", None)
    if len(canonical_json(facts)) > FACTS_MAX_CHARS - 80:
        facts = {key: facts[key] for key in (
            "schemaVersion", "evidenceVersion", "runId", "instId", "bar",
            "round", "asOfTs",
            "priceUnit", "evidenceFingerprint",
        )}
        facts.update(factsUnavailable=True, reason="facts_unavailable")
    facts["factsHash"] = _hash(facts)
    return facts


@dataclass(frozen=True)
class RoundDecisionRecord:
    """聚合本轮原始提议、最终决策及执行事实；深拷贝隔离调用方。"""

    proposal: dict | None
    final_decision: dict
    facts: dict
    facts_hash: str = ""
    execution_receipt: dict | None = None
    gate_veto: dict | None = None
    sizing_verdict: dict | None = None
    closed_bar_ts: float | None = None
    advisory_evidence: dict | None = None
    reflection_evidence: dict | None = None

    def __post_init__(self):
        for name in ("proposal", "final_decision", "facts",
                     "execution_receipt", "gate_veto", "sizing_verdict",
                     "advisory_evidence", "reflection_evidence"):
            object.__setattr__(self, name, deepcopy(getattr(self, name)))

    def to_wire(self) -> dict:
        """返回可直接写入工具结果和 journal 的独立 camelCase 快照。"""
        return deepcopy({
            "schemaVersion": 2,
            "evidenceVersion": self.facts.get("evidenceVersion"),
            "advisoryEvidence": self.advisory_evidence,
            "reflectionEvidence": self.reflection_evidence,
            "proposal": self.proposal,
            "finalDecision": self.final_decision, "facts": self.facts,
            "factsHash": self.facts_hash or self.facts.get("factsHash"),
            "executionReceipt": self.execution_receipt,
            "gateVeto": self.gate_veto, "sizingVerdict": self.sizing_verdict,
            "closedBarTs": self.closed_bar_ts,
            "evidenceFingerprint": self.facts.get("evidenceFingerprint"),
            "costModelVersion": (self.facts.get("costSnapshot") or {}).get(
                "modelVersion"),
        })


def _foldable(entry: dict) -> bool:
    from .advisory_evidence import has_complete_evidence
    if not has_complete_evidence(entry):
        return False
    if entry.get("legacy") or not entry.get("evidenceFingerprint"):
        return False
    if entry.get("trigger") != "interval" or entry.get("gateVeto"):
        return False
    if (entry.get("proposal") or {}).get("action") != "hold":
        return False
    final = entry.get("finalDecision") or entry.get("decision") or {}
    if final.get("action") != "hold" or entry.get("executionReceipt"):
        return False
    reflection = entry.get("reflection") or {}
    sizing = entry.get("sizingVerdict") or {}
    if reflection.get("vetoed") or reflection.get("verdict") == "abort":
        return False
    if (sizing.get("vetoed") or sizing.get("zeroed") or sizing.get("blocked")
            or sizing.get("floorVerdict") in (
                "solved_zero", "below_gear_floor")):
        return False
    executed = entry.get("executed") or {}
    if executed.get("action") not in (None, "hold"):
        return False
    return not (executed.get("traded") or executed.get("error")
                or executed.get("ok") is False
                or executed.get("status") in ("error", "unknown")
                or executed.get("executionReceipt")
                or (entry.get("facts") or {}).get("factsUnavailable"))


def can_fold_rounds(left: dict, right: dict) -> bool:
    """仅相邻且无否决的真实 hold 能折叠；旧记录一律保留。"""
    if not _foldable(left) or not _foldable(right):
        return False
    if left["evidenceFingerprint"] != right["evidenceFingerprint"]:
        return False
    from .advisory_evidence import has_same_evidence
    if not has_same_evidence(left, right):
        return False
    keys = ("position", "restingOrder", "pendingOpen", "rules",
            "entryQuota", "costSnapshot")
    before, after = left.get("facts") or {}, right.get("facts") or {}
    if any(not before.get(key) or before.get(key) != after.get(key)
           for key in ("runId", "instId", "bar")):
        return False
    return all(_evidence_value(before.get(key)) ==
               _evidence_value(after.get(key)) for key in keys)
