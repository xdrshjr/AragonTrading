"""`severity` 的算术性质与排序稳定性（ai-trade-intel-actionability §3.1.1）。

`severity` 决定「一轮里的哪一条会被送去打断一次决策」——它必须是**单调的、有界的、可复现的**。
一个会随调用顺序变化的严重度，会让「为什么是这一条」这个问题永远没有答案。
"""
from __future__ import annotations

import pytest

from app.ai_auto.intel.alert import AlertCandidate, age_minutes, plan_alert, severity
from app.ai_auto.intel.taxonomy import IntelLimits

NOW = 1_788_110_000.0
LIMITS = IntelLimits(alert_enabled=True, alert_max_event_age_min=180.0,
                     alert_min_impact="high", alert_min_domains=1,
                     alert_min_confidence=0.0, alert_require_quote=False,
                     alert_categories=frozenset({"risk"}))


def _candidate(**over) -> AlertCandidate:
    base = {"item_id": 1, "headline": "h", "category": "risk", "tier": "ultra",
            "stance": "bearish", "impact": "high", "confidence": 0.5,
            "event_ts": int(NOW - 1800), "captured_ts": int(NOW), "domain_count": 2,
            "quoted": False, "is_scheduled": False, "verdict": "", "summary": "s"}
    return AlertCandidate(**{**base, **over})


def _sev(**over) -> float:
    return severity(_candidate(**over), now=NOW, limits=LIMITS)


def test_severity_is_bounded_to_the_unit_interval():
    """四项加权的和被 `clamp01` 收在 [0,1]，再乘一个 [0,1] 的新鲜度。"""
    best = _sev(impact="high", domain_count=99, quoted=True, confidence=1.0,
                event_ts=int(NOW))
    worst = _sev(impact="low", domain_count=0, quoted=False, confidence=0.0,
                 event_ts=int(NOW - 10 * 3600))
    assert 0.0 <= worst <= best <= 1.0


@pytest.mark.parametrize("field,low,high", [
    ("impact", "medium", "high"),
    ("domain_count", 1, 3),
    ("quoted", False, True),
    ("confidence", 0.2, 0.9),
])
def test_every_component_is_monotonically_increasing(field, low, high):
    """四个分量各自单调：把任一项调高，严重度只会变大，绝不变小。"""
    assert _sev(**{field: low}) < _sev(**{field: high})


def test_domain_count_saturates_at_three_independent_sources():
    """来源数在 3 个之后饱和：第 4、第 10 个域名不再增加「值得打断一次决策」的程度。"""
    assert _sev(domain_count=3) == pytest.approx(_sev(domain_count=30))
    assert _sev(domain_count=2) < _sev(domain_count=3)


def test_freshness_decays_linearly_to_zero_at_the_window_edge():
    assert _sev(event_ts=int(NOW)) == pytest.approx(_sev(event_ts=int(NOW - 90 * 60)) * 2,
                                                    rel=1e-6)
    assert _sev(event_ts=int(NOW - 180 * 60)) == pytest.approx(0.0, abs=1e-9)
    assert _sev(event_ts=int(NOW - 600 * 60)) == 0.0      # clamp，不会变成负数


def test_age_falls_back_to_the_capture_time_when_the_event_time_is_unknown():
    """无事件时刻的条目取 `captured_ts`——不该因为「时间未知」就被判成无限旧。"""
    assert age_minutes(_candidate(event_ts=0, captured_ts=int(NOW - 600)),
                       now=NOW) == pytest.approx(10.0)


def test_ranking_is_stable_for_equal_severities():
    """同分时按候选顺序取第一条：一个会随调用顺序变化的结果说不清「为什么是这一条」。"""
    twins = [_candidate(item_id=1), _candidate(item_id=2)]
    assert plan_alert(twins, limits=LIMITS, now=NOW, last_fired_ts=0,
                      day_fired=0).candidate.item_id == 1
    assert plan_alert(list(reversed(twins)), limits=LIMITS, now=NOW, last_fired_ts=0,
                      day_fired=0).candidate.item_id == 2


def test_the_reported_severity_belongs_to_the_reported_candidate():
    """`AlertDecision.severity` 与 `AlertDecision.candidate` **必须同源**。

    报了 A 的分数、却把 B 投影进台账，是一种既不会报错、也永远查不出来的错位。
    """
    weak = _candidate(item_id=1, confidence=0.1)
    strong = _candidate(item_id=2, confidence=0.9)
    decision = plan_alert([weak, strong], limits=LIMITS, now=NOW, last_fired_ts=0,
                          day_fired=0)
    assert decision.candidate.item_id == 2
    assert decision.severity == pytest.approx(severity(strong, now=NOW, limits=LIMITS))
