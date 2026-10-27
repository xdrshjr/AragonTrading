"""实证准入闸纯核心（ai-trade-evidence-gated-capital §7.2，E-1..E-18）。"""
from __future__ import annotations

import math
import statistics

import pytest

from app.ai_auto.decider import TradeDecision
from app.ai_auto.edge_gate import _veto
from app.ai_auto.evidence_gate import (
    EvidenceRule,
    apply_evidence_gate,
    bucket_stats,
    resolve_evidence_rule,
    resolve_state,
    select_samples,
)
from app.ai_auto.shadow_ledger import ref_policy_from_settings
from app.ai_auto.turnover_discipline import REVERSE_MARKER
from app.config import get_settings

RULE = EvidenceRule()


def _open(action="open_long"):
    return TradeDecision(action, 0.5, 0.8, "model")


def _stats(values, rule=RULE):
    return bucket_stats(values, rule)


def test_e1_below_min_samples_is_unproven_but_mean_is_computed():
    out = _stats([5.0] * 7)
    assert out.state == "unproven" and out.mean_bps == pytest.approx(5.0) and out.n == 7


def test_e2_e3_e4_three_states():
    assert _stats([4.0, 6.0] * 5).state == "proven"
    assert _stats([-4.0, -6.0] * 5).state == "disproven"
    assert _stats([10.0, -10.0] * 5).state == "unproven"


def test_e5_standard_error_uses_sample_stdev():
    out = _stats([1.0, 3.0])
    assert out.se_bps == pytest.approx(statistics.stdev([1.0, 3.0]) / math.sqrt(2))


def test_e6_zero_dispersion_judged_by_mean_sign():
    assert _stats([2.0] * 10).state == "proven"
    assert _stats([-2.0] * 10).state == "disproven"


def test_e7_pooled_can_only_tighten():
    bucket = _stats([1.0] * 3)
    pooled_bad = _stats([-5.0, -6.0] * 10)
    out = resolve_state(bucket, pooled_bad, RULE)
    assert out.state == "disproven" and out.basis == "pooled"
    pooled_good = _stats([5.0, 6.0] * 10)
    assert resolve_state(bucket, pooled_good, RULE).state == "unproven"


@pytest.mark.parametrize("action", ["close", "hold", "cancel_order"])
def test_e8_non_open_actions_are_exempt(action):
    decision = TradeDecision(action, None, None, "x")
    out = apply_evidence_gate(decision, None, RULE, position_side="long")
    assert out.lane == "exempt" and out.decision is decision and out.reason is None


def test_e9_observe_passes_original_object():
    decision = _open()
    out = apply_evidence_gate(decision, _stats([-5.0] * 20),
                              EvidenceRule(policy="observe"), position_side="flat")
    assert out.lane == "pass" and out.decision is decision


def test_e10_missing_stats_treated_as_unproven():
    out = apply_evidence_gate(_open(), None, RULE, position_side="flat")
    assert out.lane == "shadow" and out.state == "unproven"
    assert out.reason.startswith("evidence_unproven(")


def test_e11_strict_unproven_reason_shape():
    out = apply_evidence_gate(_open(), _stats([1.0, 1.6] * 3 + [1.3]), RULE,
                              position_side="flat")
    assert out.reason == "evidence_unproven(n=7/10,mean=+1.3bps)"


def test_e12_balanced_only_blocks_disproven():
    rule = EvidenceRule(policy="balanced")
    decision = _open()
    assert apply_evidence_gate(decision, _stats([1.0] * 3), rule,
                               position_side="flat").decision is decision
    out = apply_evidence_gate(decision, _stats([-5.0, -7.0] * 6), rule, position_side="flat")
    assert out.lane == "shadow" and out.reason.startswith("evidence_disproven(n=12,")


def test_e13_e17_flat_block_matches_edge_gate_veto_shape():
    out = apply_evidence_gate(_open(), None, RULE, position_side="flat")
    ref, _ = _veto(out.reason)
    assert out.decision == ref


def test_e14_resolve_rule():
    settings = get_settings()
    assert resolve_evidence_rule(None, settings).policy == settings.ai_trade_evidence_default_policy
    assert resolve_evidence_rule({"enabled": False}, settings) is None
    off = settings.model_copy(update={"ai_trade_evidence_gate_enabled": False})
    assert resolve_evidence_rule({"enabled": True}, off) is None
    assert resolve_evidence_rule({"policy": "balanced"}, settings).policy == "balanced"
    with pytest.raises(ValueError):
        resolve_evidence_rule({"policy": "yolo"}, settings)


def test_e15_reverse_downgrades_to_close_keeping_exit_leg():
    out = apply_evidence_gate(_open("open_short"), None, RULE, position_side="long")
    assert out.decision.action == "close"
    assert out.reason.startswith("evidence_") and out.reason.endswith(f"{REVERSE_MARKER})")


def test_e16_scale_in_is_held():
    out = apply_evidence_gate(_open("open_long"), None, RULE, position_side="long")
    assert out.decision.action == "hold"


def test_e18_ref_policy_ignores_profit_lock_master_switch():
    settings = get_settings()
    off = settings.model_copy(update={"ai_trade_profit_lock_enabled": False})
    assert ref_policy_from_settings(off).lock.enabled is True
    assert ref_policy_from_settings(off).version() == ref_policy_from_settings(settings).version()


def test_select_samples_window_and_age():
    now = 1_000_000.0
    rows = [(now - 40 * 86400, -9.0)] + [(now - i, float(i)) for i in range(40, 0, -1)]
    kept = select_samples(rows, RULE, now=now)
    assert len(kept) == 30 and -9.0 not in kept and kept[-1] == 1.0
