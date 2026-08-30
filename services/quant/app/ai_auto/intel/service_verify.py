"""核查轮的完整编排（ai-trade-intel-closed-loop §3.3.5）。

与 `service.run_curate_round` **同构**：只读短会话取数 → 检索与 LLM **全程无会话** → 写会话
一次提交（不变式 I-4：任何 DB 会话都不得跨越一次 LLM 或检索的 `await`；SQLite 是单写者）。

三条本模块独有的判断：

1. **核查轮自判日配额**（评审 P1-5）。配额闸今天只写在 `run_collect_round` 里一处，而
   `day_rounds` 由 `bump_intel_watch` 对**每一种** kind 自增。不自判的话，核查轮会**吃掉**采集
   的额度、自己却不受额度约束——最坏的组合是「采集因为配额停了，核查还在烧钱」。
2. **语料为空是一个真实的核查结论，不是一次失败**。全部候选记 `unresolved` 并 `verify_count+1`
   ——记成失败会让同一批候选在下一轮被原样再查一次，那是烧钱且什么都不产出的路径。
3. **不在这里刷新快照**（评审 P2-7）。`IntelRuntime._run` 的 `_after_round` 已经无条件
   `await self.refresh(...)`；在编排里再刷一次是同一轮里的第二次全量查库，而两次结果之间的
   差异**没有任何东西会告诉你**。
"""
from __future__ import annotations

import logging
import math

from ...db import repositories as repo
from . import corpus as corpus_mod
from . import evidence as evidence_mod
from . import prompts
from . import quotes as quotes_mod
from . import verify
from .agent import run_round
from .service import (
    IntelDeps,
    RoundOutcome,
    daily_used,
    finish_degraded,
    item_from_wire,
    next_due_for,
)

logger = logging.getLogger(__name__)


def _verify_queries(items, *, inst_id: str) -> list[str]:
    """每条候选生成**一条**查询，由服务端**确定性**构造（§3.3.2）。

    **绝不让模型自己写查询**——那是它「先想好结论再去找证据」最短的路。
    """
    base = corpus_mod.parse_inst(inst_id)[0]
    return [f"{item.headline} {base} 最新 进展" for item in items]


def _freshness_days(items, *, now: float) -> int:
    """只看**事件之后**的报道：`max(1, ceil((now - event_ts)/86400) + 1)`。"""
    oldest = min((int(it.event_ts or it.captured_ts or 0) for it in items), default=0)
    if oldest <= 0:
        return 3
    return max(1, math.ceil((float(now) - oldest) / 86400.0) + 1)


async def _persist_verify(deps: IntelDeps, inst_id: str, *, started: int, plans,
                          counts: dict, doc_count: int, domain_count: int,
                          degrade_code: str, tokens: tuple[int, int],
                          quote_rows: list[dict] | None = None,
                          reader_counts: dict | None = None,
                          quotes_proposed: int = 0) -> RoundOutcome:
    """核查轮的写段：判定落库 → 写轮 → 引文落库 → 推节律，**一次提交**。"""
    usage_in, usage_out = tokens
    status = "degraded" if degrade_code else "ok"
    rows = quote_rows or []
    async with deps.sessionmaker()() as db:
        applied = await repo.apply_intel_verdicts(
            db, [{"itemId": plan.item_id, "verdict": plan.verdict, "reason": plan.reason,
                  "confidence": plan.confidence, "toTier": plan.to_tier,
                  "expiresTs": plan.expires_ts if plan.to_tier else None,
                  "overwriteVerdict": plan.overwrite_verdict,
                  "verifyCount": plan.verify_count,
                  # ★ evidence-grounding 评审 P1-2：这张 wire 是**逐列手抄**的第三处。不加这一行，
                  # `VerdictPlan.resolve_schedule` 会一直是 True 而库里那一列恒为 0，任何只断言
                  # dataclass 的用例都会绿。
                  "resolveSchedule": plan.resolve_schedule}
                 for plan in plans], now=started, commit=False)
        histogram = {**counts, **{f"applied_{k}": v for k, v in applied.items()}}
        # ⚠️ **先写轮存根再写引文**：引文行上的 `round_id` 只有拿到 round id 之后才填得出来，
        # 而它是「这段核查原文是哪一轮产生的」在审计上的唯一线索。
        round_id = await repo.write_intel_round(db, {
            "instId": inst_id, "kind": "verify", "groupId": deps.group_id,
            "startedTs": started, "finishedTs": int(deps.now()), "status": status,
            "searchProvider": deps.search.slug if deps.search else "",
            "docCount": doc_count, "domainCount": domain_count, "actions": histogram,
            "inputTokens": usage_in, "outputTokens": usage_out,
            "degradeCode": degrade_code,
            # evidence-grounding 评审 P1-6：核查轮同样带四个计数。不带的后果不是「少两列」，
            # 而是命中率的分母**系统性偏小** ⇒ 把 `quote_mode` 翻到 strict 的判据永远达不成。
            "readerAttempt": int((reader_counts or {}).get("readerAttempt", 0)),
            "readerOk": int((reader_counts or {}).get("readerOk", 0)),
            "quotesProposed": int(quotes_proposed),
            "quotesVerified": len(rows)}, commit=False)
        await repo.write_intel_quotes(db, [{**row, "roundId": round_id} for row in rows],
                                      commit=False)
        # ★ 评审 P0-3：走**第三个**分支——写 `last_verify_ts`、清 `curates_since_verify`、
        # **绝不动 `last_curate_ts`**（G-16 冻结「上次整理一秒都没变」）。
        await repo.bump_intel_watch(db, inst_id, kind="verify", status=status,
                                    next_due_ts=next_due_for(deps, fail_streak=0),
                                    verify_reset=True, commit=False)
        await db.commit()
    return RoundOutcome(inst_id, "verify", status, degrade_code, actions=histogram)


async def run_verify_round(deps: IntelDeps, inst_id: str, *,
                           only_item_id: int | None = None) -> RoundOutcome:
    """跑一轮核查：选候选 → 重新检索 → Agent 判定 → 服务端复核 → 确定性后果。

    **绝不抛**（`CancelledError` 除外，那是关停信号）。任何失败翻译成一个降级码。

    Args:
        deps: 依赖聚合。
        inst_id: 目标标的。
        only_item_id: 只核查这一条（`POST /intel/items/{itemId}/verify`，§4.1 #21）。
            给了它就**显式绕过 `plan_verification` 的全部候选过滤**——`origin` / `verify_count` /
            `user_edited` / `verify_min_age_h` 一概不看（评审 P2-11）。它是一条人类显式指令，
            不是自动池的一员；这也是用户手写条目与「我不同意」过的条目唯一的核查途径。
            **日配额仍然生效**（额度是成本闸，手动触发不该能绕过它）。

    Returns:
        `RoundOutcome`。候选为空 ⇒ **不写 round 行**（沿用「未到期不写 round 行」的纪律：一张
        全是空轮的审计表等于没有审计表）。
    """
    now = deps.now()
    started = int(now)
    # ① 只读短会话：取 watch 行与库存全集，取完立刻关闭（不变式 I-4）。
    async with deps.sessionmaker()() as db:
        watch = await repo.load_intel_watch(db, inst_id) or {}
        scope = await repo.load_intel_scope(db, inst_id)
        blocked = frozenset(await repo.list_intel_blocked_domains(db, inst_id))
    items = [item_from_wire(w) for w in scope]
    if only_item_id:
        candidates = tuple(it for it in items if it.item_id == int(only_item_id))
    else:
        candidates = verify.plan_verification(items, limits=deps.limits, now=now)
    if not candidates:
        return RoundOutcome(inst_id, "verify", "ok", actions={"verify_skipped": 1})
    # ①·5 日配额自判（评审 P1-5）：不自判的话核查轮会 bump `day_rounds`（吃掉采集的额度）
    # 却自己不受任何约束。
    if daily_used(watch, now=now) >= int(deps.settings.ai_trade_intel_daily_round_cap):
        return await finish_degraded(deps, inst_id, kind="verify", started=started,
                                     code="budget_exhausted", status="degraded")
    if deps.search is None:
        return await finish_degraded(deps, inst_id, kind="verify", started=started,
                                     code="verify_no_provider", status="degraded")

    # ②–④ 全程**无会话**：检索与 LLM 都在这一段。
    queries = _verify_queries(candidates, inst_id=inst_id)
    built, ctx, degrade = await _gather_verify_corpus(deps, inst_id, queries=queries,
                                                      blocked=blocked, now=now,
                                                      candidates=candidates)
    if degrade == "verify_search_failed":
        return await finish_degraded(deps, inst_id, kind="verify", started=started,
                                     code=degrade, status="failed", queries=queries)
    if built is None:
        # 语料为空：**全部候选记 `unresolved`**——「查不到」是一个真实的核查结论，不是一次失败。
        plans = verify.plan_all_unresolved(candidates, note="本轮未检索到可核对的新材料")
        return await _persist_verify(deps, inst_id, started=started, plans=plans,
                                     counts={"unresolved": len(plans)}, doc_count=0,
                                     domain_count=0, degrade_code="verify_no_corpus",
                                     tokens=(0, 0))
    # ③·5 / ③·6（evidence-grounding §3.1.1 的核查轮那一列）：**复用同一份缓存、同一条
    # `reader_round_deadline_s`、同一个 `reader_top_k` 上限**，不按 `verify_max_items ×
    # verify_docs_per_item` 扇出（§十三 Q3 的裁决）。
    # ⚠️ 用的必须是 `_gather_verify_corpus` **建库时的那个 `ctx`**，不能在这里另造一个：
    # `attach_bodies` 会拿它整体重渲染 `block`，两个 ctx 今天恰好等价，但只要日后有人给建库那侧
    # 补上 `existing_headlines` 或 `change_24h_pct`，这里就会**静默地把它们从语料块里抹掉**。
    built, reader_counts = await evidence_mod.attach_document_bodies(deps, ctx, built)
    return await _run_verify_agent(deps, inst_id, started=started, candidates=candidates,
                                   corpus=built, now=now, reader_counts=reader_counts)


async def _gather_verify_corpus(deps: IntelDeps, inst_id: str, *, queries: list[str],
                                blocked, now: float, candidates):
    """核查轮的检索 + 装语料。**全程无 DB 会话**。

    Returns:
        `(Corpus, IntelContext, None)`；检索失败 `(None, ctx, "verify_search_failed")`；
        零文档 `(None, ctx, None)`。**`ctx` 一并回出**：抓回正文之后 `attach_bodies` 要拿它
        整体重渲染语料块，而另造一个等价的 ctx 只是把「哪一天它们不再等价」推给下一个人。
    """
    from .search import IntelSearchError

    ctx = corpus_mod.build_context(inst_id=inst_id, bar=deps.bar, now_wall=int(now))
    try:
        docs = await deps.search.search_many(
            queries, limit=int(deps.settings.ai_trade_intel_verify_docs_per_item),
            freshness_days=_freshness_days(candidates, now=now))
    except IntelSearchError as exc:
        logger.info("ai-trade intel verify search failed for %s: %s", inst_id, exc.message)
        return None, ctx, "verify_search_failed"
    built = corpus_mod.build(
        docs, ctx, max_docs=int(deps.settings.ai_trade_intel_corpus_max_docs),
        snippet_max_chars=int(deps.settings.ai_trade_intel_snippet_max_chars),
        blocked_domains=blocked)
    return (built, ctx, None) if built.docs else (None, ctx, None)


async def _run_verify_agent(deps: IntelDeps, inst_id: str, *, started: int, candidates,
                            corpus, now: float,
                            reader_counts: dict | None = None) -> RoundOutcome:
    """调核查 Agent 并把判定过一遍服务端复核表。

    **装配顺序是定死的**（evidence-grounding 评审 P1-3）：引文比对必须在这里先做，产出的
    `quotes_by_ref` 再作为**必填 keyword-only** 传进 `review_verdicts`——纯核心手里只有
    `doc_refs` 这个字符串集合，没有任何正文，判不了「这条判定有没有已验证引文」。
    """
    # ★ 评审 P1-3：`verify_llm is None` ⇒ **回落 `deps.llm`**（用采集轮的预算跑，而不是报错）。
    llm = deps.verify_llm or deps.llm
    if llm is None:
        return await finish_degraded(deps, inst_id, kind="verify", started=started,
                                     code="verify_no_provider", status="degraded",
                                     doc_count=len(corpus.docs), corpus=corpus,
                                     reader_counts=reader_counts)
    result = await run_round(
        llm,
        system=prompts.build_verify_system_prompt(deps.limits),
        user=prompts.build_verify_user_prompt(
            prompts.render_verify_candidates(candidates, now=now), corpus),
        tool_name=prompts.VERIFY_TOOL_NAME,
        tool_description=prompts.VERIFY_TOOL_DESCRIPTION,
        tool_schema=prompts.build_verify_tool_schema(deps.limits),
        timeout_s=float(deps.settings.ai_trade_intel_timeout_s))
    if result.degrade:
        return await finish_degraded(deps, inst_id, kind="verify", started=started,
                                     code="verify_llm_failed", status="degraded",
                                     doc_count=len(corpus.docs), corpus=corpus,
                                     usage=result, reader_counts=reader_counts)
    verdicts = list(result.raw.get("verdicts") or [])
    quotes_by_ref, quote_rejects = quotes_mod.verify_verdict_quotes(
        verdicts, corpus.index, limits=deps.limits)
    indexed = {verify.format_item_ref(i + 1): item for i, item in enumerate(candidates)}
    review = verify.review_verdicts(verdicts, indexed, tuple(corpus.index.keys()),
                                    limits=deps.limits, now=now,
                                    quotes_by_ref=quotes_by_ref)
    counts = {**dict(review.counts), **quote_rejects}
    rows = _verify_quote_rows(inst_id, plans=review.plans, indexed=indexed,
                              quotes_by_ref=quotes_by_ref, now=int(now))
    return await _persist_verify(deps, inst_id, started=started, plans=review.plans,
                                 counts=counts, doc_count=len(corpus.docs),
                                 domain_count=corpus.domain_count, degrade_code="",
                                 tokens=(result.input_tokens, result.output_tokens),
                                 quote_rows=rows, reader_counts=reader_counts,
                                 quotes_proposed=quotes_mod.count_proposed(verdicts))


def _verify_quote_rows(inst_id: str, *, plans, indexed: dict, quotes_by_ref: dict,
                       now: int) -> list[dict]:
    """核查引文 → `write_intel_quotes` 的 payload（`role="verify"`）。

    只落**判定真的写下去了**的那些条目的引文：一条被整条丢弃的判定（`bad_item_ref` /
    `bad_verdict`）没有宿主，落库只会写出孤儿行。
    """
    kept = {int(plan.item_id) for plan in plans}
    rows: list[dict] = []
    for token, quotes in (quotes_by_ref or {}).items():
        item = indexed.get(token)
        if item is None or int(item.item_id or 0) not in kept:
            continue
        rows.extend(evidence_mod.quote_row(inst_id, int(item.item_id), 0, quote,
                                           role="verify", now=now)
                    for quote in quotes)
    return rows
