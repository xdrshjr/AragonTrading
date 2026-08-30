"""核查候选选取与判定复核表（ai-trade-intel-closed-loop §7.1 · test_ai_intel_verify）。

轴 C 的纯核心：全部用例零 IO、零网络、零 DB。这一层是「入口有闸、出口也有闸」这条承诺的落点，
也是 R-3（把「没找到」判成「被推翻」）唯一的服务端硬闸所在。
"""
from __future__ import annotations

from app.ai_auto.intel import verify
from app.ai_auto.intel.gate import IntelItem
from app.ai_auto.intel.taxonomy import IntelLimits

NOW = 1_800_000_000
LIMITS = IntelLimits()
DOC_REFS = ("D1", "D2", "D3")


def _item(item_id: int, **over) -> IntelItem:
    base = dict(item_id=item_id, inst_id="BTC-USDT-SWAP", tier="mid", category="macro",
                headline=f"标题{item_id}", summary="摘要", stance="neutral", impact="high",
                confidence=0.6, event_ts=NOW - 5 * 86400, captured_ts=NOW - 5 * 86400,
                expires_ts=NOW + 86400, origin="agent", status="active")
    base.update(over)
    return IntelItem(**base)


def _verdict(ref: str, verdict: str, *, reason: str = "语料明确写到了这件事", refs=("D1",)):
    return {"itemRef": ref, "verdict": verdict, "reason": reason, "refs": list(refs)}


def _review(entries, candidates):
    indexed = {verify.format_item_ref(i + 1): it for i, it in enumerate(candidates)}
    return verify.review_verdicts(entries, indexed, DOC_REFS, limits=LIMITS)


# ---------------- 候选选取（§3.3.1） ----------------
def test_scheduled_event_past_due_ranks_first():
    """P1 级：「解锁到底发生了没有」是本层最硬的可证伪问题。"""
    scheduled = _item(1, is_scheduled=True, event_ts=NOW - 60)
    plain = _item(2)
    picked = verify.plan_verification([plain, scheduled], limits=LIMITS, now=NOW)

    assert picked[0].item_id == 1


def test_pinned_item_is_still_a_candidate():
    """G-8 的前半：免死金牌管的是**后果**（不被自动降级），不是**检查**。"""
    picked = verify.plan_verification([_item(1, pinned=True)], limits=LIMITS, now=NOW)

    assert [it.item_id for it in picked] == [1]


def test_user_items_and_user_edited_are_never_auto_verified():
    """用户写下的东西轮不到模型去判真假；手改过的同样（`user_edited` 兼任判定锁）。"""
    picked = verify.plan_verification(
        [_item(1, origin="user"), _item(2, user_edited=True)], limits=LIMITS, now=NOW)

    assert picked == ()


def test_too_young_item_is_not_a_candidate():
    """太新 ⇒ 后续报道还没出来，核查它只会得到一个 `unresolved` 并白烧一次预算。"""
    fresh = _item(1, captured_ts=NOW - 60, event_ts=NOW - 60)
    assert verify.plan_verification([fresh], limits=LIMITS, now=NOW) == ()


def test_candidates_are_capped_to_verify_max_items():
    limits = IntelLimits(verify_max_items=2)
    picked = verify.plan_verification([_item(i) for i in range(1, 6)], limits=limits, now=NOW)

    assert len(picked) == 2


def test_cleared_verdict_item_is_not_reverified():
    """G-17 / P0-4：用户点完「我不同意」（清 verdict、置 user_edited、**不清 verify_count**）
    之后，**连跑三轮核查它都不再进候选池**。"""
    cleared = _item(1, verdict="", verdict_ts=0, verify_count=1, user_edited=True)

    for _round in range(3):
        assert verify.plan_verification([cleared], limits=LIMITS, now=NOW) == ()


def test_refuted_item_reenters_pool_for_the_second_strike():
    """P1-14：两击制要求 `refuted` 也能被第二次核查（P3 判据收它）。"""
    struck = _item(1, verdict="refuted", verdict_ts=NOW - 3 * 86400, verify_count=1)

    assert [it.item_id for it in verify.plan_verification([struck], limits=LIMITS, now=NOW)] == [1]


def test_recheck_respects_the_cooldown_and_the_attempt_cap():
    too_soon = _item(1, verdict="unresolved", verdict_ts=NOW - 60, verify_count=1)
    exhausted = _item(2, verdict="unresolved", verdict_ts=NOW - 9 * 86400, verify_count=3)

    assert verify.plan_verification([too_soon, exhausted], limits=LIMITS, now=NOW) == ()


# ---------------- 复核表（§3.3.4） ----------------
def test_refuted_without_refs_becomes_unresolved():
    """G-7：无有效 `D` 引用的非 `unresolved` 判定一律降级，并**不执行任何降级后果**。"""
    item = _item(1)
    review = _review([_verdict("C1", "refuted", refs=())], [item])

    assert review.counts.get("verdict_no_ref") == 1
    assert len(review.plans) == 1
    plan = review.plans[0]
    assert plan.verdict == "unresolved"
    assert plan.to_tier is None and plan.confidence is None


def test_bad_doc_ref_is_dropped_but_the_row_survives():
    """`refs` 里不在 D 表的编号**逐个**丢弃，不丢整条。"""
    review = _review([_verdict("C1", "confirmed", refs=("D1", "D9"))], [_item(1)])

    assert review.counts.get("bad_ref") == 1
    assert review.plans[0].verdict == "confirmed"
    assert review.plans[0].refs == ("D1",)


def test_c_ref_is_not_accepted_as_d_ref():
    """R-11：三套编号空间互斥——`C1` 写进 `refs` 一律丢弃并计 `bad_ref`。"""
    review = _review([_verdict("C1", "confirmed", refs=("C1",))], [_item(1)])

    assert review.counts.get("bad_ref") == 1
    assert review.plans[0].verdict == "unresolved"     # 有效 refs 空 ⇒ 降级


def test_unknown_item_ref_and_bad_verdict_are_dropped_whole():
    review = _review([_verdict("C9", "confirmed"), _verdict("C1", "maybe")], [_item(1)])

    assert review.counts.get("bad_item_ref") == 1
    assert review.counts.get("bad_verdict") == 1
    assert review.plans == ()


def test_duplicate_item_ref_keeps_first():
    review = _review([_verdict("C1", "confirmed"), _verdict("C1", "refuted")], [_item(1)])

    assert review.counts.get("dup_item_ref") == 1
    assert [p.verdict for p in review.plans] == ["confirmed"]


def test_empty_reason_is_downgraded_to_unresolved():
    review = _review([_verdict("C1", "confirmed", reason="   ")], [_item(1)])

    assert review.counts.get("verdict_no_reason") == 1
    assert review.plans[0].verdict == "unresolved"


def test_unresolved_does_not_overwrite_confirmed():
    """「这次没查出来」不该抹掉「上次查实了」。"""
    review = _review([_verdict("C1", "unresolved")], [_item(1, verdict="confirmed")])

    assert review.plans[0].overwrite_verdict is False


def test_first_refuted_plans_no_tier_change():
    """P1-12 的纯核心侧：首击只写 verdict + 罚置信度，`tier` 一个字节都不动。"""
    review = _review([_verdict("C1", "refuted")], [_item(1, tier="long", verify_count=0)])
    plan = review.plans[0]

    assert plan.verdict == "refuted"
    assert plan.to_tier is None
    assert plan.confidence == 0.3          # 0.6 × 0.5
    assert plan.verify_count == 1


def test_second_refuted_demotes_one_tier_and_recomputes_expires():
    """第二击才降一层，且 `expires_ts` **必须在同一条计划里**按新 tier 重算（不变式 I-3）。"""
    review = _review([_verdict("C1", "refuted")], [_item(1, tier="long", verify_count=1)])
    plan = review.plans[0]

    assert plan.to_tier == "mid"
    assert plan.expires_ts > 0
    assert plan.verify_count == 2


def test_refuted_never_archives_even_at_ultra():
    """P1-12：任何情形都不因 refuted 归档——`VerdictPlan` 里根本没有 status 这个出口。"""
    review = _review([_verdict("C1", "refuted")], [_item(1, tier="ultra", verify_count=2)])
    plan = review.plans[0]

    assert plan.to_tier is None            # 已在最低层 ⇒ 不再降，也不归档
    assert not hasattr(plan, "status")


def test_protected_refuted_keeps_tier_and_confidence():
    """G-8：受保护条目无论第几击都只写 verdict / 理由 / 次数。"""
    review = _review([_verdict("C1", "refuted")],
                     [_item(1, tier="long", verify_count=3, pinned=True)])
    plan = review.plans[0]

    assert plan.verdict == "refuted"
    assert plan.to_tier is None and plan.confidence is None


def test_confirmed_bonus_is_capped_at_one():
    review = _review([_verdict("C1", "confirmed")], [_item(1, confidence=0.99)])

    assert review.plans[0].confidence == 1.0


def test_plan_all_unresolved_bumps_every_candidate():
    """语料为空是一个**真实的核查结论**：全部候选记 `unresolved` 并 `verify_count+1`。"""
    plans = verify.plan_all_unresolved([_item(1), _item(2, verdict="confirmed")], note="查不到")

    assert [p.verdict for p in plans] == ["unresolved", "unresolved"]
    assert [p.verify_count for p in plans] == [1, 1]
    assert plans[1].overwrite_verdict is False
