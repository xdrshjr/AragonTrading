"""轴 D：已排期事件的兑现与失效（evidence-grounding §7.1 · test_ai_intel_schedule_resolution）。

G-10 / G-11 / G-23 在这里。

**F-1 是一处既有缺陷**：`apply_intel_verdicts` 末尾无条件写 `updated_ts = now`，而
`plan_cleanup` 的失效判据带 `updated_ts <= event_ts`；核查候选的最高优先级 P1 恰恰是
「已排期且时刻已过」。于是一条「9 月 3 日主网升级」在 9 月 3 日之后**第一次**被核查，无论
判成什么，都会**永久**退出失效清理——而界面上没有任何异样。

`test_verified_scheduled_item_still_lapses` 就是那条回归护栏：它在修法之前必须是红的。
"""
from __future__ import annotations

import time

import pytest
from sqlalchemy import delete

from app.ai_auto.intel import cleanup, verify
from app.ai_auto.intel.gate import IntelItem
from app.ai_auto.intel.service import item_from_wire
from app.ai_auto.intel.taxonomy import IntelLimits
from app.db import repositories as repo
from app.db.models import AiIntelItem
from app.db.session import get_sessionmaker, init_db

NOW = int(time.time()) + 3600
INST = "SOL-USDT-SWAP"
LIMITS = IntelLimits()


def _scheduled(**over) -> IntelItem:
    """一条 36 小时前就该发生、至今没人兑现的已排期条目。"""
    base = dict(item_id=1, inst_id=INST, tier="mid", category="project",
                headline="主网 v2 升级定档", summary="主网 v2 升级已定档，届时将启用新费率模型。",
                stance="bullish", impact="high", confidence=0.7,
                event_ts=NOW - 36 * 3600, captured_ts=NOW - 5 * 86400,
                expires_ts=NOW + 30 * 86400, is_scheduled=True, domain_count=2)
    base.update(over)
    return IntelItem(**base)


# ---------------- G-10：F-1 的回归护栏（改代码前必须是红的） ----------------
def test_verified_scheduled_item_still_lapses():
    """被核查过的已排期条目**仍然**会失效降层。

    合入前这条是红的：核查轮把 `updated_ts` 推到 `event_ts` 之后，该条目从此永久退出这条清理。
    修法不是删掉那半条判据，而是把它换成一个只表达「这件事已经兑现」的专用列——删掉它会让
    「用户手改过的已排期条目」落进自动降层（虽然 `protected` 已在更早一行拦了一道）。
    """
    verified = _scheduled(updated_ts=NOW - 100, verdict="unresolved", verify_count=1)
    plan = cleanup.plan_cleanup([verified], limits=LIMITS, now=NOW,
                                blocked_domains=frozenset())
    assert [action.item_id for action in plan.lapsed] == [1]
    assert plan.lapsed[0].reason == "lapsed_schedule"


def test_resolved_scheduled_item_stops_lapsing():
    resolved = _scheduled(schedule_resolved_ts=NOW - 3600)
    plan = cleanup.plan_cleanup([resolved], limits=LIMITS, now=NOW,
                                blocked_domains=frozenset())
    assert plan.lapsed == () and plan.expire_ids == ()


def test_protected_item_is_still_skipped_before_the_criterion():
    """删掉 `updated_ts` 那半条不会伤到用户手改过的条目：`protected` 在更早一行就 `continue` 了。"""
    for over in ({"pinned": True}, {"user_edited": True}):
        plan = cleanup.plan_cleanup([_scheduled(**over)], limits=LIMITS, now=NOW,
                                    blocked_domains=frozenset())
        assert plan.lapsed == ()


# ---------------- 纯核心：兑现只对**已发生**的排期事件成立 ----------------
def _review(item: IntelItem, *, now: float):
    return verify.review_verdicts(
        [{"itemRef": "C1", "verdict": "confirmed", "reason": "升级已按计划完成", "refs": ["D1"]}],
        {"C1": item}, ("D1",), limits=LIMITS, now=now, quotes_by_ref={})


def test_resolve_only_fires_for_past_scheduled_items():
    past = _review(_scheduled(), now=NOW).plans[0]
    assert past.resolve_schedule is True

    future = _review(_scheduled(event_ts=NOW + 5 * 86400), now=NOW).plans[0]
    assert future.resolve_schedule is False, \
        "一条**未来**的排期事件被判 confirmed 就落兑现位 ⇒ 它从此永不失效，那正是 F-1 复发"

    not_scheduled = _review(_scheduled(is_scheduled=False), now=NOW).plans[0]
    assert not_scheduled.resolve_schedule is False


def test_unresolved_does_not_write_resolved_ts():
    review = verify.review_verdicts(
        [{"itemRef": "C1", "verdict": "unresolved", "reason": "没有后续报道"}],
        {"C1": _scheduled()}, ("D1",), limits=LIMITS, now=NOW, quotes_by_ref={})
    assert review.plans[0].resolve_schedule is False


def test_review_verdicts_has_no_default_now():
    """`now` 与 `quotes_by_ref` **都没有默认值**：漏传必须是 `TypeError`，不是静默错判。"""
    with pytest.raises(TypeError):
        verify.review_verdicts([], {}, (), limits=LIMITS)


# ---------------- G-23：端到端（从 `_plan_confirmed` 到 DB 行） ----------------
async def _seed(**over) -> int:
    await init_db()
    async with get_sessionmaker()() as session:
        await session.execute(delete(AiIntelItem).where(AiIntelItem.inst_id == INST))
        await session.commit()
        payload = {"instId": INST, "tier": "mid", "category": "project",
                   "headline": "主网 v2 升级定档", "summary": "主网 v2 升级已定档。",
                   "stance": "bullish", "impact": "high", "confidence": 0.7,
                   "eventTs": NOW - 36 * 3600, "capturedTs": NOW - 5 * 86400,
                   "expiresTs": NOW + 30 * 86400, "status": "active", "isScheduled": True,
                   "origin": "agent", "dedupKey": "sched-1", "sources": [], "domainCount": 2}
        payload.update(over)
        ids = await repo.upsert_intel_items(session, [payload])
    return ids[0]


@pytest.mark.asyncio
async def test_confirmed_scheduled_item_lands_the_resolution_in_the_db():
    """G-23：**直接查 DB 行**——只断言 `VerdictPlan.resolve_schedule` 的用例会一直是绿的。"""
    item_id = await _seed()
    async with get_sessionmaker()() as session:
        before = await repo.get_intel_item(session, item_id)
        plan = _review(item_from_wire(before), now=NOW).plans[0]
        await repo.apply_intel_verdicts(session, [{
            "itemId": plan.item_id, "verdict": plan.verdict, "reason": plan.reason,
            "confidence": plan.confidence, "toTier": plan.to_tier, "expiresTs": None,
            "overwriteVerdict": plan.overwrite_verdict, "verifyCount": plan.verify_count,
            "resolveSchedule": plan.resolve_schedule}], now=NOW)
        after = await repo.get_intel_item(session, item_id)

    assert after["scheduleResolvedTs"] == NOW
    assert after["isScheduled"] is False
    assert after["expiresTs"] == before["expiresTs"], "G-11：兑现**不改** expiresTs"

    # 兑现之后，同一条不再出现在失效清理里（读出通路三跳全通才成立）。
    plan = cleanup.plan_cleanup([item_from_wire(after)], limits=LIMITS, now=NOW,
                                blocked_domains=frozenset())
    assert plan.lapsed == ()


@pytest.mark.asyncio
async def test_future_scheduled_item_confirmed_does_not_resolve_in_the_db():
    item_id = await _seed(eventTs=NOW + 5 * 86400, dedupKey="sched-2")
    async with get_sessionmaker()() as session:
        row = await repo.get_intel_item(session, item_id)
        plan = _review(item_from_wire(row), now=NOW).plans[0]
        await repo.apply_intel_verdicts(session, [{
            "itemId": plan.item_id, "verdict": plan.verdict, "reason": plan.reason,
            "confidence": plan.confidence, "toTier": None, "expiresTs": None,
            "overwriteVerdict": True, "verifyCount": plan.verify_count,
            "resolveSchedule": plan.resolve_schedule}], now=NOW)
        after = await repo.get_intel_item(session, item_id)
    assert after["scheduleResolvedTs"] == 0 and after["isScheduled"] is True


@pytest.mark.asyncio
async def test_reupsert_does_not_reset_quote_stats():
    """G-12：4 个新 item 列**绝不进** `_item_values` ⇒ 同题再报不清零。"""
    item_id = await _seed(dedupKey="sched-3")
    async with get_sessionmaker()() as session:
        await repo.write_intel_quote_state(session, {item_id: (True, 2, "")})
        await repo.apply_intel_verdicts(session, [{
            "itemId": item_id, "verdict": "confirmed", "reason": "已完成", "confidence": None,
            "toTier": None, "expiresTs": None, "overwriteVerdict": True, "verifyCount": 1,
            "resolveSchedule": True}], now=NOW)
        await repo.upsert_intel_items(session, [{
            "instId": INST, "tier": "mid", "category": "project", "headline": "主网 v2 升级定档",
            "summary": "再次被报道。", "stance": "bullish", "impact": "high", "confidence": 0.7,
            "eventTs": NOW - 36 * 3600, "capturedTs": NOW, "expiresTs": NOW + 30 * 86400,
            "status": "active", "isScheduled": True, "origin": "agent",
            "dedupKey": "sched-3", "sources": [], "domainCount": 2}])
        after = await repo.get_intel_item(session, item_id)
    assert after["quoteCount"] == 2 and after["quoted"] is True
    assert after["scheduleResolvedTs"] == NOW, "兑现位被同题再报清零 ⇒ 该条目会重新开始失效倒计时"
