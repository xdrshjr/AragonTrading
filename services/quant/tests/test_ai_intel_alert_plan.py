"""告警判定纯核心的判据表（ai-trade-intel-actionability G-4 / G-5 / G-12 / G-31）。

**离线、零 IO**：`plan_alert` 是纯函数，这正是「唤醒一次决策是会花真钱的动作，它的判据必须能被
逐条冻结、回放、反证」那条纪律的兑现处。

12 条抑制码**各有一条用例**，每条构造一个「只差那一个判据」的候选，断言 `suppressed_code`
**精确等于**目标码——不是「不为空」。一条只断言「被抑制了」的用例，在判据表整体写错时照样绿。
"""
from __future__ import annotations

import pytest

from app.ai_auto.intel.alert import AlertCandidate, candidates_from_round, plan_alert, severity
from app.ai_auto.intel.taxonomy import ALERT_SUPPRESS_CODES, IntelLimits

NOW = 1_788_110_000.0


def _limits(**over) -> IntelLimits:
    """默认路径的阈值（**总闸开着**）：G-4 的每条用例都跑在默认阈值上（Q1 兑现条件 ②）。"""
    base = {"alert_enabled": True, "alert_min_impact": "high",
            "alert_categories": frozenset({"risk", "macro", "project"}),
            "alert_require_quote": True, "alert_min_domains": 2,
            "alert_min_confidence": 0.6, "alert_max_event_age_min": 180.0,
            "alert_cooldown_min": 20.0, "alert_daily_cap": 6,
            "alert_allow_scheduled": False}
    return IntelLimits(**{**base, **over})


def _candidate(**over) -> AlertCandidate:
    """一条**必然 fired** 的候选：改一个字段就够格触发对应的那一条抑制码。"""
    base = {"item_id": 41, "headline": "某交易所热钱包异常出金 1.2 万枚 ETH",
            "category": "risk", "tier": "ultra", "stance": "bearish", "impact": "high",
            "confidence": 0.78, "event_ts": int(NOW - 16 * 60), "captured_ts": int(NOW),
            "domain_count": 3, "quoted": True, "is_scheduled": False, "verdict": "",
            "summary": "三个独立来源报道该交易所热钱包在过去一小时异常出金。"}
    return AlertCandidate(**{**base, **over})


def _plan(candidates, *, limits=None, last_fired_ts=0, day_fired=0):
    return plan_alert(list(candidates), limits=limits or _limits(), now=NOW,
                      last_fired_ts=last_fired_ts, day_fired=day_fired)


def test_a_fully_qualified_candidate_fires():
    decision = _plan([_candidate()])
    assert decision.fired is True
    assert decision.suppressed_code == ""
    assert decision.candidate is not None and decision.candidate.item_id == 41
    assert 0.0 < decision.severity <= 1.0
    assert set(decision.reasons) == {"impactW", "freshness", "ageMin", "domainCount",
                                     "confidence", "quoted"}


# ---------- G-4：12 条抑制码逐条冻结 ----------
@pytest.mark.parametrize(("kwargs", "expected"), [
    ({"limits": _limits(alert_enabled=False)}, "disabled"),
    ({"candidates": []}, "no_candidate"),
    ({"day_fired": 6}, "daily_cap"),
    ({"last_fired_ts": int(NOW - 60)}, "cooldown"),
    ({"candidates": [_candidate(impact="medium")]}, "impact_below_bar"),
    ({"candidates": [_candidate(category="sentiment")]}, "category_out"),
    ({"candidates": [_candidate(verdict="refuted")]}, "refuted"),
    ({"candidates": [_candidate(is_scheduled=True)]}, "scheduled_future"),
    ({"candidates": [_candidate(event_ts=int(NOW - 5 * 3600))]}, "stale_event"),
    ({"candidates": [_candidate(domain_count=1)]}, "few_domains"),
    ({"candidates": [_candidate(confidence=0.55)]}, "low_confidence"),
    ({"candidates": [_candidate(quoted=False)]}, "no_quote"),
])
def test_every_suppression_code_is_reachable_with_a_single_failing_criterion(kwargs, expected):
    payload = {"candidates": [_candidate()], **kwargs}
    decision = _plan(payload["candidates"],
                     limits=payload.get("limits"),
                     last_fired_ts=payload.get("last_fired_ts", 0),
                     day_fired=payload.get("day_fired", 0))
    assert decision.fired is False
    assert decision.suppressed_code == expected


def test_every_declared_suppression_code_has_a_test():
    """判据表与值域表**逐值一致**：新增一个码却没有用例，就是一条永远走不到的分支。"""
    covered = {
        "disabled", "no_candidate", "daily_cap", "cooldown", "impact_below_bar",
        "category_out", "refuted", "scheduled_future", "stale_event", "few_domains",
        "low_confidence", "no_quote",
    }
    assert covered == set(ALERT_SUPPRESS_CODES)


# ---------- G-5：一轮至多一条 ----------
def test_only_the_highest_severity_candidate_fires():
    """两条同样够格 ⇒ 恰好一条 fired，且是 `severity` 更高的那条。

    两条同时唤醒只会撞在同一个 `_deciding` 槽位上，第二条必然被丢弃——那不如根本不发。
    """
    weak = _candidate(item_id=1, domain_count=2, confidence=0.61)
    strong = _candidate(item_id=2, domain_count=5, confidence=0.95)
    decision = _plan([weak, strong])
    assert decision.fired is True
    assert decision.candidate.item_id == 2
    assert severity(strong, now=NOW, limits=_limits()) > severity(weak, now=NOW,
                                                                  limits=_limits())


# ---------- G-12（纯核心一侧）：轮级抑制仍要说出被压下去的是哪一条（评审 P2-11） ----------
@pytest.mark.parametrize("kwargs", [{"day_fired": 6}, {"last_fired_ts": int(NOW - 60)}])
def test_round_level_suppression_still_projects_the_top_candidate(kwargs):
    decision = _plan([_candidate(item_id=7)], **kwargs)
    assert decision.fired is False
    assert decision.candidate is not None and decision.candidate.item_id == 7
    assert decision.candidate.headline


def test_empty_round_reports_no_candidate_and_carries_no_projection():
    decision = _plan([])
    assert (decision.fired, decision.suppressed_code, decision.candidate) == (
        False, "no_candidate", None)


# ---------- G-31：merges 分支能触发告警（评审 P1-4） ----------
class _Merge:
    def __init__(self, item_id, dedup_key, domain_count, updated_ts):
        self.item_id = item_id
        self.dedup_key = dedup_key
        self.domain_count = domain_count
        self.updated_ts = updated_ts
        self.prior_quoted = True


class _Admitted:
    def __init__(self, accepted=(), merges=()):
        self.accepted = tuple(accepted)
        self.merges = tuple(merges)
        self.quotes = {}


class _Prior:
    """`existing[dedup_key]` 的最小替身（`gate.IntelItem` 的只读字段）。"""

    headline = "某交易所热钱包异常出金 1.2 万枚 ETH"
    summary = "首报时只有一个来源。"
    category = "risk"
    tier = "ultra"
    stance = "bearish"
    impact = "high"
    confidence = 0.78
    event_ts = int(NOW - 16 * 60)
    captured_ts = int(NOW - 3600)
    is_scheduled = False
    verdict = ""


def test_a_second_domain_reporting_the_same_story_can_fire():
    """「同一条风险情报被第二个域名再次报道」**只走 merges 分支**，而它恰恰是 `min_domains>=2`
    最该放行的那一类。

    反例：若实现只投影 `accepted`，本条必红——那正是评审 P1-4 指出的缺口。
    """
    merge = _Merge(item_id=41, dedup_key="k1", domain_count=2, updated_ts=int(NOW))
    candidates = candidates_from_round(_Admitted(merges=[merge]), {"k1": _Prior()},
                                       item_ids=[])
    assert len(candidates) == 1
    # 内容字段取自**旧条目**，`domain_count` 取合并**之后**的新值。
    assert candidates[0].headline == _Prior.headline
    assert candidates[0].category == "risk"
    assert candidates[0].domain_count == 2
    assert candidates[0].captured_ts == int(NOW)
    assert _plan(candidates).fired is True


def test_accepted_candidates_are_keyed_by_the_upsert_return_value():
    """`item_id` 只能来自 `upsert_intel_items` 的返回值，且**按入参顺序对齐**。"""
    class _Item(_Prior):
        def __init__(self, headline):
            self.headline = headline
            self.quoted = True
            self.domain_count = 3

    admitted = _Admitted(accepted=[_Item("a"), _Item("b")])
    candidates = candidates_from_round(admitted, {}, item_ids=[11, 12])
    assert [(c.item_id, c.headline) for c in candidates] == [(11, "a"), (12, "b")]
    # 返回值短于 accepted ⇒ 多出来的那几条**被跳过**（没有 id 就落不了台账、也回写不了）。
    assert len(candidates_from_round(admitted, {}, item_ids=[11])) == 1


# ---------- severity 的两条算术性质 ----------
def test_low_impact_scores_zero_by_arithmetic_not_by_a_separate_rule():
    """`taxonomy.IMPACT_W["low"] == 0.0` 让「低影响力不告警」成为一条算术性质。

    取错成 `gate.SCORE_IMPACT_W`（`low = 0.34`）不会报错，只会让低影响力条目拿到一个正分。
    """
    low = severity(_candidate(impact="low"), now=NOW, limits=_limits())
    high = severity(_candidate(impact="high"), now=NOW, limits=_limits())
    mid = severity(_candidate(impact="medium"), now=NOW, limits=_limits())
    freshness = 1.0 - (16.0 / 180.0)
    # 影响力项的**全部**贡献是 ：low 那一档恰好是 0，
    # 故三档之间的差值必须精确等于这条算术。取错成  会让 low 拿到 0.34 的
    # 权重 —— 差值当场对不上，而它不会以任何别的方式报错。
    assert high - low == pytest.approx(0.45 * 1.0 * freshness, abs=1e-9)
    assert mid - low == pytest.approx(0.45 * 0.5 * freshness, abs=1e-9)


def test_a_future_event_is_never_stale():
    """未来事件的年龄为负 ⇒ `freshness` 恒为 1.0（不会因为「还没发生」被判成陈旧）。"""
    future = _candidate(event_ts=int(NOW + 7200), is_scheduled=True)
    assert severity(future, now=NOW, limits=_limits()) == pytest.approx(
        severity(_candidate(event_ts=int(NOW)), now=NOW, limits=_limits()), abs=1e-9)
