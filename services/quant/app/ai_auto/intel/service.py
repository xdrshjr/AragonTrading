"""采集轮与整理轮的完整编排（ai-trade-market-intel §3.3 / §3.4）。

**不变式 I-4（会话边界，评审 P1-8）：任何 DB 会话都不得跨越一次 LLM 或检索的 `await`。**
SQLite 是单写者——一个横跨 60 秒 LLM 调用的写事务会把**全库**其它写入（成交落库、run metrics、
经验层物化）一起阻塞数十秒。落地形态：

- 采集轮：步骤 1 开一个**只读**短会话取 watch 行与库存后立即关闭；步骤 2–7 全程**无会话**
  （检索与 LLM 都在这一段）；步骤 8–9 再开一个写会话，`upsert_items` + `write_round` +
  `bump_watch` **一次提交**。
- 整理轮：取快照用只读短会话；LLM 全程无会话；复核 → 执行 → 确定性清理 → 写轮在一个写会话内
  一次提交。§3.4.4 那句「在模型动作执行之后、事务提交之前」描述的是**写会话内部**的次序，
  不是「事务从轮开始就开着」。

第二条贯穿本模块的纪律：**情报失败绝不阻断真实资金的交易路径**。每一条失败路径都翻译成一个
降级码 + 一行 round 存根 + 一次退避，绝不向上抛。
"""
from __future__ import annotations

import logging
import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ...db import repositories as repo
from . import corpus as corpus_mod
from . import gate, prompts
from . import taxonomy as tax
from .agent import run_round
from .disclosure import build_digest
from .gate import IntelItem, IntelSource
from .search import IntelSearchError, SearchRunner
from .taxonomy import IntelLimits, limits_from_settings

logger = logging.getLogger(__name__)


# ---------------- 到期 / 退避 / 日配额（纯函数） ----------------
def backoff_seconds(fail_streak: int, *, base_s: float, max_s: float) -> float:
    """失败退避：`0 if n == 0 else min(base * 2**(n-1), max)`。"""
    n = max(0, int(fail_streak))
    return 0.0 if n == 0 else min(float(base_s) * (2 ** (n - 1)), float(max_s))


def next_due_after(*, now: float, interval_min: float, jitter_pct: float,
                   fail_streak: int, base_s: float, max_s: float,
                   rng: Callable[[], float] | None = None) -> int:
    """下一次到期时刻 = now + 周期×(1±jitter) + backoff(fail_streak)。

    抖动的作用不是玄学：N 个标的同时到期会一起打第三方检索源的限流（R-10）。
    """
    roll = (rng or random.random)()
    factor = 1.0 + (roll * 2.0 - 1.0) * max(0.0, float(jitter_pct))
    span = float(interval_min) * 60.0 * factor
    return int(now + span + backoff_seconds(fail_streak, base_s=base_s, max_s=max_s))


def is_due(watch: dict, *, now: float) -> bool:
    """该标的现在是否该跑一轮采集。"""
    if not watch or not watch.get("enabled", True):
        return False
    if int(watch.get("cooldownUntilTs") or 0) > now:
        return False
    return int(watch.get("nextDueTs") or 0) <= now


def daily_used(watch: dict, *, now: float | None = None) -> int:
    """当前 UTC 日已用轮数（跨日即视作 0——`day_key` 是唯一判据）。"""
    if not watch:
        return 0
    today = repo.intel_utc_day_key(now)
    return int(watch.get("dayRounds") or 0) if watch.get("dayKey") == today else 0


def verify_due(settings, watch: dict) -> bool:
    """该标的现在是否该在整理轮**之前**插一轮核查（closed-loop §3.3.6）。

    判据是 `curates_since_verify >= verify_every_n_curates`，而那个计数器的**唯一自增点**在
    `bump_intel_watch` 的 `curate` 分支（评审 P0-3）。没有自增点时它恒 0 ⇒ 核查轮一次都不会跑，
    且界面上不会有任何异样。

    ⚠️ **判据里不带 `+1`**，尽管紧邻的整理轮判据（`collects_since_curate + 1 >= every`）带。
    两者形状相同但时序相反，这是终审改掉的一处真缺陷：

    - 整理轮的 `+1` 算的是**刚刚跑完**的那一次采集（`run_collect` 已经 bump 过，但 `_tick`
      读的是 tick 开头的快照），所以要补回来；
    - 核查轮排在整理轮**之前**，此刻「距上次核查跑过几次整理」正好就是计数器本身。更要命的是
      `verify_reset` 清零之后，**同一个 tick 的整理轮立刻把它加回 1** —— 稳态下这个计数器永远
      不会被观察到 0。再 `+1`，稳态周期就塌成 `N-1`：把设置调到 2 的用户会得到「从第二次整理起
      每次都核查」，检索与 LLM 账单翻倍，而界面上同样没有任何异样。它是 P0-3 的镜像
      （那一条是「一次都不跑」，这一条是「每次都跑」）。
    """
    if not bool(getattr(settings, "ai_trade_intel_verify_enabled", False)):
        return False
    every = max(1, int(getattr(settings, "ai_trade_intel_verify_every_n_curates", 1)))
    return int((watch or {}).get("curatesSinceVerify") or 0) >= every


# ---------------- 领域对象 ⇄ wire ----------------
def item_from_wire(wire: dict) -> IntelItem:
    """repo 的 camelCase wire → 纯核心 `IntelItem`（**唯一**映射点）。"""
    sources = tuple(
        IntelSource(url=str(s.get("url") or ""), title=str(s.get("title") or ""),
                    publisher=str(s.get("publisher") or ""), domain=str(s.get("domain") or ""),
                    published_ts=int(s.get("publishedTs") or 0))
        for s in (wire.get("sources") or []) if isinstance(s, dict))
    return IntelItem(
        item_id=wire.get("id"), inst_id=wire.get("instId", ""), tier=wire.get("tier", "ultra"),
        category=wire.get("category", "market"), headline=wire.get("headline", ""),
        summary=wire.get("summary", ""), stance=wire.get("stance", "neutral"),
        impact=wire.get("impact", "medium"), confidence=float(wire.get("confidence") or 0.0),
        event_ts=int(wire.get("eventTs") or 0), captured_ts=int(wire.get("capturedTs") or 0),
        expires_ts=int(wire.get("expiresTs") or 0), sources=sources,
        domain_count=int(wire.get("domainCount") or 0), status=wire.get("status", "active"),
        pinned=bool(wire.get("pinned")), user_edited=bool(wire.get("userEdited")),
        is_scheduled=bool(wire.get("isScheduled")), undated=bool(wire.get("undated")),
        origin=wire.get("origin", "agent"), dedup_key=wire.get("dedupKey", ""),
        updated_ts=int(wire.get("updatedTs") or 0),
        # ---- closed-loop §3.2.3：**读**方向必须带上这 10 个字段（`score_item` 的 cite 加分与
        # `build_digest` 的 refuted 过滤都依赖它们）。写方向的 `item_to_payload` 一个都不许带。
        cite_count=int(wire.get("citeCount") or 0),
        cite_entry_count=int(wire.get("citeEntryCount") or 0),
        settled_count=int(wire.get("settledCount") or 0),
        win_count=int(wire.get("winCount") or 0),
        net_pnl_usd=float(wire.get("netPnlUsd") or 0.0),
        last_cited_ts=int(wire.get("lastCitedTs") or 0),
        verdict=wire.get("verdict", "") or "",
        verdict_ts=int(wire.get("verdictTs") or 0),
        verdict_note=wire.get("verdictNote", "") or "",
        verify_count=int(wire.get("verifyCount") or 0))


def item_to_payload(item: IntelItem, *, group_id: str = "", round_id: int = 0) -> dict:
    """纯核心 `IntelItem` → repo 入库 payload。

    ⚠️ **closed-loop 的 10 个新列一个都不许出现在这里**（评审 P1-6）：`upsert_intel_items` 的
    冲突分支用 `_item_values` 的键集减 `_PRESERVED_ON_CONFLICT` 求 `update_cols`，统计列一旦
    进了写方向，同一条情报被再次报道时全部引用战绩会被**清零**且不报任何错（G-20 冻结这条）。
    """
    return {
        "instId": item.inst_id, "tier": item.tier, "category": item.category,
        "headline": item.headline, "summary": item.summary, "stance": item.stance,
        "impact": item.impact, "confidence": item.confidence, "eventTs": item.event_ts,
        "capturedTs": item.captured_ts, "expiresTs": item.expires_ts, "status": item.status,
        "pinned": item.pinned, "userEdited": item.user_edited, "undated": item.undated,
        "isScheduled": item.is_scheduled, "origin": item.origin,
        "originGroupId": group_id, "originRoundId": round_id,
        "sources": [{"url": s.url, "title": s.title, "publisher": s.publisher,
                     "domain": s.domain, "publishedTs": s.published_ts} for s in item.sources],
        "domainCount": item.domain_count, "dedupKey": item.dedup_key,
    }


# ---------------- 依赖聚合 ----------------
@dataclass
class IntelDeps:
    """一轮情报所需的全部依赖（**显式注入**，使离线用例不必造一个真的会话）。"""

    settings: object
    sessionmaker: Callable[[], object]
    search: SearchRunner | None = None
    llm: object | None = None
    # 核查轮的**第二个** client（closed-loop 评审 P1-3）：`verify_max_tokens` 只能走构造参。
    # ``None`` ⇒ **回落 `llm`**（用采集轮的预算跑，而不是报错——一个因为预算装配没做好就整轮
    # 不跑的核查层，比一个预算稍大的核查层坏得多）。
    verify_llm: object | None = None
    group_id: str = ""
    bar: str = "15m"
    change_24h_pct: Callable[[str], float | None] | None = None
    now: Callable[[], float] = time.time
    limits: IntelLimits = field(default=None)   # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.limits is None:
            self.limits = limits_from_settings(self.settings)


@dataclass(frozen=True)
class RoundOutcome:
    """一轮的结果（写库之后的回执；`notify` 与 REST 202 都读它）。"""

    inst_id: str
    kind: str
    status: str                  # ok | degraded | failed
    degrade_code: str = ""
    accepted: int = 0
    merged: int = 0
    rejected: int = 0
    actions: dict = field(default_factory=dict)
    error_text: str = ""


# ---------------- 采集轮 ----------------
async def run_collect_round(deps: IntelDeps, inst_id: str, *,
                            force: bool = False) -> RoundOutcome | None:
    """跑一轮采集。**绝不抛**（`CancelledError` 除外，那是关停信号）。

    Args:
        deps: 依赖聚合。
        inst_id: 目标标的（恒为完整 instId，不做资产级归并）。
        force: 手动触发（`POST /intel/{instId}/collect`）时跳过到期判定，但**不跳过日配额**——
            额度是成本闸，手动触发不该能绕过它。

    Returns:
        `RoundOutcome`；未到期而返回 ``None``（**不写 round 行**——那会让「整理记录」页被一堆
        「跳过」刷屏，而它们不携带任何信息）。
    """
    now = deps.now()
    started = int(now)
    # ① 只读短会话：取 watch 行与库存全集，取完立刻关闭（不变式 I-4）。
    # 早退判定在会话**外**兑现：`finish_degraded` 自己要开一个写会话，在这里直接调它会造出
    # 一次嵌套会话——SQLite 单写者下那是一条自己等自己的路径。
    async with deps.sessionmaker()() as db:
        watch = await repo.ensure_intel_watch(db, inst_id)
        due = force or is_due(watch, now=now)
        over_cap = daily_used(watch, now=now) >= int(deps.settings.ai_trade_intel_daily_round_cap)
        scope = await repo.load_intel_scope(db, inst_id)
        blocked = frozenset(await repo.list_intel_blocked_domains(db, inst_id))
        # closed-loop 评审 P1-7：信任清单与黑名单在**同一个只读会话**里取。少这一行，用户点下的
        # 「更信任这个源」就是一次什么都没发生的点击，而这件事不会报任何错。
        trusted = frozenset(await repo.list_intel_trusted_domains(db, inst_id))
    if not due:
        return None
    if over_cap:
        return await finish_degraded(deps, inst_id, kind="collect", started=started,
                                     code="budget_exhausted", status="degraded")
    if deps.search is None or deps.llm is None:
        return await finish_degraded(deps, inst_id, kind="collect", started=started,
                                     code="no_provider", status="degraded")

    existing = {w["dedupKey"]: item_from_wire(w) for w in scope if w.get("dedupKey")}
    headlines = tuple(w["headline"] for w in scope if w.get("status") == "active")
    ctx = corpus_mod.build_context(
        inst_id=inst_id, bar=deps.bar, now_wall=int(now),
        change_24h_pct=deps.change_24h_pct(inst_id) if deps.change_24h_pct else None,
        existing_headlines=headlines)

    # ②–⑦ 全程**无会话**：检索与 LLM 都在这一段。
    queries = corpus_mod.plan_queries(ctx, count=int(deps.settings.ai_trade_intel_queries_per_round))
    built, failed = await _gather_corpus(deps, inst_id, ctx, blocked=blocked, queries=queries,
                                         started=started, trusted=trusted)
    if built is None:
        return failed
    result = await run_round(
        deps.llm,
        system=prompts.build_collect_system_prompt(ctx, deps.limits),
        user=prompts.build_collect_user_prompt(built),
        tool_name=prompts.COLLECT_TOOL_NAME,
        tool_description=prompts.COLLECT_TOOL_DESCRIPTION,
        tool_schema=prompts.build_collect_tool_schema(deps.limits),
        timeout_s=float(deps.settings.ai_trade_intel_timeout_s))
    if result.degrade:
        return await finish_degraded(deps, inst_id, kind="collect", started=started,
                                     code=result.degrade, status="degraded", queries=queries,
                                     doc_count=len(built.docs), corpus=built, usage=result)
    admitted = gate.admit(list(result.raw.get("items") or []), built, existing,
                          inst_id=inst_id, limits=deps.limits, now=now,
                          trusted_domains=trusted)
    return await _persist_collect(deps, inst_id, started=started, queries=queries,
                                  corpus=built, admitted=admitted, usage=result)


async def _gather_corpus(deps: IntelDeps, inst_id: str, ctx, *, blocked, queries: list[str],
                         started: int, trusted=frozenset()):
    """采集轮的步骤 ④–⑤：并发检索 + 装语料。**全程无 DB 会话**（不变式 I-4）。

    Returns:
        `(Corpus, None)`；检索失败或零文档时 `(None, RoundOutcome)`——后者已经把降级落好了轮存根
        与退避。**零文档时绝不调 LLM**：那只会烧钱并诱发幻觉。
    """
    try:
        docs = await deps.search.search_many(
            queries, limit=int(deps.settings.ai_trade_intel_docs_per_query),
            freshness_days=3)
    except IntelSearchError as exc:
        code = "circuit_open" if exc.code == "circuit_open" else "search_failed"
        return None, await finish_degraded(deps, inst_id, kind="collect", started=started,
                                           code=code, status="failed", error=exc.message,
                                           queries=queries)
    built = corpus_mod.build(docs, ctx,
                             max_docs=int(deps.settings.ai_trade_intel_corpus_max_docs),
                             snippet_max_chars=int(deps.settings.ai_trade_intel_snippet_max_chars),
                             blocked_domains=blocked, trusted_domains=trusted)
    if not built.docs:
        return None, await finish_degraded(deps, inst_id, kind="collect", started=started,
                                           code="search_empty", status="degraded",
                                           queries=queries, doc_count=0)
    return built, None


async def _persist_collect(deps: IntelDeps, inst_id: str, *, started: int,
                           queries: list[str], corpus, admitted, usage) -> RoundOutcome:
    """采集轮的步骤 ⑧–⑨：落条目 + 落轮 + 推节律，**一次提交**（不变式 I-4 的写段）。"""
    status = "ok" if (admitted.accepted or admitted.merges) else "degraded"
    degrade_code = "" if status == "ok" else "all_rejected"
    async with deps.sessionmaker()() as db:
        round_id = await repo.write_intel_round(db, {
            "instId": inst_id, "kind": "collect", "groupId": deps.group_id,
            "startedTs": started, "finishedTs": int(deps.now()), "status": status,
            "searchProvider": deps.search.slug if deps.search else "", "queries": queries,
            "docCount": len(corpus.docs), "domainCount": corpus.domain_count,
            "accepted": len(admitted.accepted), "rejected": admitted.total_rejected,
            "merged": len(admitted.merges), "rejects": admitted.rejects,
            "inputTokens": usage.input_tokens, "outputTokens": usage.output_tokens,
            "degradeCode": degrade_code}, commit=False)
        if admitted.accepted:
            await repo.upsert_intel_items(
                db, [item_to_payload(it, group_id=deps.group_id, round_id=round_id)
                     for it in admitted.accepted], commit=False)
        for merge in admitted.merges:
            await repo.merge_intel_sources(
                db, merge.item_id,
                sources=[{"url": s.url, "title": s.title, "publisher": s.publisher,
                          "domain": s.domain, "publishedTs": s.published_ts}
                         for s in merge.sources],
                domain_count=merge.domain_count, expires_ts=merge.expires_ts,
                revive=merge.revive, updated_ts=merge.updated_ts, commit=False)
        await repo.bump_intel_watch(
            db, inst_id, kind="collect", status=status,
            next_due_ts=next_due_for(deps, fail_streak=0), commit=False)
        await db.commit()
    return RoundOutcome(inst_id, "collect", status, degrade_code, len(admitted.accepted),
                        len(admitted.merges), admitted.total_rejected)


# ---------------- 整理轮 ----------------
async def run_curate_round(deps: IntelDeps, inst_id: str) -> RoundOutcome:
    """跑一轮整理：模型提议 → 服务端复核 → 执行 → **确定性清理** → 写轮。

    确定性清理（TTL / 溢出 / 已排期失效 / 孤儿 superseded）**不依赖模型**：模型哑火时噪声照样
    被清。这正是「噪声或无效的短线情报可以进行整理或清理」这条需求的兜底实现。
    """
    now = deps.now()
    started = int(now)
    async with deps.sessionmaker()() as db:
        scope = await repo.load_intel_scope(db, inst_id)
        # closed-loop 评审 P1-8：整理轮的只读会话此前**不查**黑名单（只有采集轮查）。不补这一行，
        # `plan_cleanup` 的存量扫除就永远算出空集——而 `blocked_domains` 是必填形参，漏传会是
        # `TypeError` 而不是静默空转，两条一起才根治。
        blocked = frozenset(await repo.list_intel_blocked_domains(db, inst_id))
    items = [item_from_wire(w) for w in scope]
    live = [it for it in items if it.status == "active" and it.item_id]

    review = None
    usage_in = usage_out = 0
    degrade_code = ""
    if deps.llm is not None and live:
        result = await run_round(
            deps.llm,
            system=prompts.build_curate_system_prompt(inst_id, deps.limits),
            user=prompts.build_curate_user_prompt(render_listing(live, now=now)),
            tool_name=prompts.CURATE_TOOL_NAME,
            tool_description=prompts.CURATE_TOOL_DESCRIPTION,
            tool_schema=prompts.build_curate_tool_schema(deps.limits),
            timeout_s=float(deps.settings.ai_trade_intel_timeout_s))
        usage_in, usage_out = result.input_tokens, result.output_tokens
        if result.degrade:
            degrade_code = result.degrade
        else:
            review = gate.review_actions(list(result.raw.get("actions") or []),
                                         {int(it.item_id): it for it in live},
                                         limits=deps.limits, now=now)
    elif deps.llm is None:
        degrade_code = "no_provider"

    actions = list(review.approved) if review else []
    # 存量扫除单键（closed-loop §11）：关掉时**传空集**而不是跳过整步——`blocked_domains` 是必填
    # 形参，空集是它的诚实取值，而「跳过整步」会让 `counts["blocked_sweep"]` 这一键整键缺席。
    sweep_on = bool(getattr(deps.settings, "ai_trade_intel_blocked_sweep_enabled", True))
    # §3.4.4：确定性清理跑在**模型动作执行之后**。用动作前的快照算清理计划会让一条刚被升进中期
    # 的情报仍然按超短层参与溢出淘汰——那正是「算了却算错了对象」的形状。
    plan = gate.plan_cleanup(_after_actions(live, actions), limits=deps.limits, now=now,
                             blocked_domains=(blocked if sweep_on else frozenset()))
    counts: dict[str, int] = dict(review.counts) if review else {}
    rejects = dict(review.rejects) if review else {}
    return await _persist_curate(deps, inst_id, started=started, actions=actions, plan=plan,
                                 counts=counts, rejects=rejects, degrade_code=degrade_code,
                                 tokens=(usage_in, usage_out))


async def _persist_curate(deps: IntelDeps, inst_id: str, *, started: int, actions, plan,
                          counts: dict, rejects: dict, degrade_code: str,
                          tokens: tuple[int, int]) -> RoundOutcome:
    """整理轮的写段：复核通过的动作 → 三步确定性清理 → 写轮 → 推节律，**一次提交**。"""
    usage_in, usage_out = tokens
    async with deps.sessionmaker()() as db:
        applied = await repo.apply_intel_curation(db, [
            {"itemId": a.item_id, "action": a.action, "toTier": a.to_tier,
             "mergeIntoId": a.merge_into_id, "reason": a.reason, "expiresTs": a.expires_ts}
            for a in actions], now=started, commit=False)
        lapsed = await repo.apply_intel_curation(db, [
            {"itemId": a.item_id, "action": a.action, "toTier": a.to_tier,
             "mergeIntoId": None, "reason": a.reason, "expiresTs": a.expires_ts}
            for a in plan.lapsed], now=started, commit=False)
        await repo.archive_intel_items(db, list(plan.expire_ids), reason="ttl", now=started,
                                       commit=False)
        await repo.archive_intel_items(db, list(plan.overflow_ids), reason="overflow",
                                       now=started, commit=False)
        # closed-loop §3.4.2 第 5 步：全部来源都被拉黑的存量条目（G-9②）。
        await repo.archive_intel_items(db, list(plan.blocked_ids), reason="source_blocked",
                                       now=started, commit=False)
        await repo.purge_orphan_superseded(db, inst_id, commit=False)
        # closed-loop G-13：引用明细的保留期裁剪顺手挂在整理轮末尾。**只删明细、不回滚统计列**
        # ——统计列是已发布的读数，为了省几行明细去追溯改写它们比多留几行明细坏得多。
        pruned = await repo.prune_intel_citations(
            db, before_ts=int(started - float(getattr(
                deps.settings, "ai_trade_intel_cite_retention_d", 180.0)) * 86400),
            commit=False)
        histogram = {**counts, **{f"applied_{k}": v for k, v in applied.items()},
                     **plan.counts, **{f"rejected_{k}": v for k, v in rejects.items()}}
        if lapsed:
            histogram["applied_lapsed"] = sum(lapsed.values())
        if pruned:
            histogram["citations_pruned"] = pruned
        status = "degraded" if degrade_code else "ok"
        await repo.write_intel_round(db, {
            "instId": inst_id, "kind": "curate", "groupId": deps.group_id,
            "startedTs": started, "finishedTs": int(deps.now()), "status": status,
            "actions": histogram, "inputTokens": usage_in, "outputTokens": usage_out,
            "degradeCode": degrade_code}, commit=False)
        await repo.bump_intel_watch(db, inst_id, kind="curate", status=status,
                                    next_due_ts=next_due_for(deps, fail_streak=0),
                                    curate_reset=True, commit=False)
        await db.commit()
    return RoundOutcome(inst_id, "curate", status, degrade_code, actions=histogram)


def _after_actions(items: list[IntelItem], actions) -> list[IntelItem]:
    """把已过复核的动作投影到内存中的库存快照上（**不落库**，只为算清理计划）。

    与 `repo.apply_intel_curation` 的语义逐条对齐：`promote`/`demote` 改 tier 与寿命、`archive`
    置 `archived`、`merge` 置 `superseded`。两处一旦漂移，清理就会按一份不存在的库存来算。
    """
    from dataclasses import replace as _replace

    projected = {int(it.item_id): it for it in items if it.item_id}
    for action in actions:
        item = projected.get(int(action.item_id))
        if item is None:
            continue
        if action.action in ("promote", "demote"):
            projected[item.item_id] = _replace(item, tier=str(action.to_tier),
                                               expires_ts=int(action.expires_ts))
        elif action.action == "archive":
            projected[item.item_id] = _replace(item, status="archived")
        elif action.action == "merge":
            projected[item.item_id] = _replace(item, status="superseded")
    return list(projected.values())


def render_listing(items: list[IntelItem], *, now: float, max_rows: int = 80) -> str:
    """整理轮的紧凑清单（按层分组、层内按 score 降序、总数封顶）。"""
    from .disclosure import relative_time     # 同包复用，避免第二份相对时间口径

    rows: list[str] = []
    remaining = max_rows
    for tier in ("long", "mid", "ultra"):
        group = sorted([it for it in items if it.tier == tier],
                       key=lambda it: gate.score_item(it, now=now), reverse=True)
        if not group:
            continue
        rows.append(f"【{tax.TIER_LABELS[tier]} {len(group)} 条】")
        for item in group[:max(0, remaining)]:
            flags = "".join(["【置顶】" if item.pinned else "",
                             "【用户编辑】" if item.user_edited else "",
                             "【已排期】" if item.is_scheduled else ""])
            rows.append(
                f"#{item.item_id} [{tax.CATEGORY_LABELS[item.category]}·"
                f"{tax.STANCE_LABELS[item.stance]}·{tax.IMPACT_LABELS[item.impact]}] "
                f"置信{item.confidence:.2f} · 事件{relative_time(item.event_ts, now)} · "
                f"{item.domain_count} 源{flags} · 「{item.headline}」")
            rows.append(f"     摘要：{item.summary[:80]}")
        remaining -= len(group)
        if remaining <= 0:
            break
    return "\n".join(rows) if rows else "（本标的当前没有任何在库情报。）"


# ---------------- 降级收尾 ----------------
def next_due_for(deps: IntelDeps, *, fail_streak: int) -> int:
    """下一次**采集**到期时刻（三种轮共用同一条节律，与合入前逐字一致）。**公开名**同上。"""
    return next_due_after(
        now=deps.now(), interval_min=float(deps.settings.ai_trade_intel_collect_interval_min),
        jitter_pct=float(deps.settings.ai_trade_intel_collect_jitter_pct),
        fail_streak=fail_streak,
        base_s=float(deps.settings.ai_trade_intel_fail_backoff_base_s),
        max_s=float(deps.settings.ai_trade_intel_fail_backoff_max_s))


async def finish_degraded(deps: IntelDeps, inst_id: str, *, kind: str, started: int,
                          code: str, status: str, error: str = "",
                          queries: list[str] | None = None, doc_count: int = 0,
                          corpus=None, usage=None) -> RoundOutcome:
    """把一条失败路径落成「一行 round 存根 + 一次退避」。

    **公开名**（closed-loop）：`service_verify.run_verify_round` 的五条降级路径也走它。同包里
    跨模块 import 一个下划线私有名，等于把它的签名变成公开契约却没有任何地方说明这件事。

    **五种失败在界面上是五段不同的中文文案**（§1.2 承诺三）：一个假装没有新闻的情报面板，比一个
    明说「我这两小时没拿到任何东西」的面板危险得多——用户会把前者读成「市场很平静」。
    """
    async with deps.sessionmaker()() as db:
        watch = await repo.load_intel_watch(db, inst_id) or {}
        streak = int(watch.get("failStreak") or 0) + (1 if status == "failed" else 0)
        await repo.write_intel_round(db, {
            "instId": inst_id, "kind": kind, "groupId": deps.group_id, "startedTs": started,
            "finishedTs": int(deps.now()), "status": status,
            "searchProvider": deps.search.slug if deps.search else "",
            "queries": queries or [], "docCount": doc_count,
            "domainCount": corpus.domain_count if corpus else 0,
            "inputTokens": usage.input_tokens if usage else 0,
            "outputTokens": usage.output_tokens if usage else 0,
            "degradeCode": code, "errorText": error}, commit=False)
        cooldown = 0
        if code == "circuit_open" and deps.search is not None:
            cooldown = int(deps.now()) + deps.search.breaker.remaining_s()
        await repo.bump_intel_watch(db, inst_id, kind=kind, status=status,
                                    next_due_ts=next_due_for(deps, fail_streak=streak),
                                    cooldown_until_ts=cooldown, last_error=error,
                                    commit=False)
        await db.commit()
    logger.info("ai-trade intel %s round degraded for %s: %s", kind, inst_id, code)
    return RoundOutcome(inst_id, kind, status, code, error_text=error)


# ---------------- 快照（供 runtime 缓存；**热路径不查库**） ----------------
async def build_snapshot(deps: IntelDeps, inst_id: str) -> dict:
    """一次性算出注入用的 digest 与界面用的 summary（唯一的查库点）。

    `IntelRuntime` 在每一轮结束、用户经 REST 改动条目后、以及 `POST /seen` 之后调它刷新，
    决策热路径只读缓存（§3.6.1）。
    """
    now = deps.now()
    async with deps.sessionmaker()() as db:
        summary = await read_summary(db, inst_id, settings=deps.settings,
                                     provider_configured=deps.search is not None, now=now)
        scope = await repo.load_intel_scope(db, inst_id)
    items = [item_from_wire(w) for w in scope]
    inject_on = bool(getattr(deps.settings, "ai_trade_intel_inject", True))
    # ★ closed-loop 评审 P1-15：`cite` **只有这一个取值来源**。写在别处会让 REST 读到的库存与
    # 注入看到的库存分叉，而分叉不报错。
    cite_on = bool(getattr(deps.settings, "ai_trade_intel_cite_enabled", False))
    digest = (build_digest(items, limits=deps.limits, now=now,
                           last_collect_ts=summary["lastCollectTs"], cite=cite_on)
              if inject_on else {"lines": [], "hidden": 0})
    return {"digest": digest, "summary": summary}


async def read_summary(db, inst_id: str, *, settings, provider_configured: bool,
                       now: float | None = None) -> dict:
    """`AiTradeIntelSummary` 的**唯一**装配点（runtime 快照与 REST 读端点共用）。

    两处各拼一份的后果不是抽象的：本仓库的 `groupStateFromStatus` 就是那样一个逐键手抄的映射，
    它反复制造「WS 通路是好的，只有刷新后重发现拿不到值」这类最难发现的坑。
    """
    wall = time.time() if now is None else now
    watch = await repo.load_intel_watch(db, inst_id) or {}
    counts = await repo.count_intel_by_tier(db, inst_id)
    unread = await repo.count_intel_unread(
        db, inst_id, last_seen_ts=int(watch.get("lastSeenTs") or 0))
    impact = await repo.read_intel_impact(db, inst_id, limit=1)
    verdicts = await repo.count_intel_verdicts(db, inst_id)
    return {
        "instId": inst_id,
        "counts": {"ultra": counts["ultra"], "mid": counts["mid"], "long": counts["long"],
                   "archived": counts["archived"]},
        "unread": unread,
        "lastSeenTs": int(watch.get("lastSeenTs") or 0),
        "lastCollectTs": int(watch.get("lastCollectTs") or 0) or None,
        "lastCurateTs": int(watch.get("lastCurateTs") or 0) or None,
        "nextDueTs": int(watch.get("nextDueTs") or 0) or None,
        "inFlight": False,
        "degrade": None,
        "providerConfigured": bool(provider_configured),
        "dailyUsed": daily_used(watch, now=wall),
        "dailyCap": int(settings.ai_trade_intel_daily_round_cap),
        # ---- closed-loop §3.6：两块新读数（`snapshot()` 的第八键与 WS `intel` 事件共用同一个
        # 出口，**不新增第九键**）。`netPnlUsd` 是**关联**净额（共现统计，不是因果归因，R-1）。
        "citations": {
            "cited": int(impact["totals"]["citeCount"]),
            "entryCited": int(impact["totals"]["citeEntryCount"]),
            "settledTrips": int(impact["totals"]["settledCount"]),
            "winCount": int(impact["totals"]["winCount"]),
            "netPnlUsd": float(impact["totals"]["netPnlUsd"]),
            "neverCited": int(impact["neverCited"]),
        },
        "verify": {
            "confirmed": int(verdicts.get("confirmed", 0)),
            "refuted": int(verdicts.get("refuted", 0)),
            "unresolved": int(verdicts.get("unresolved", 0)),
            "pending": int(verdicts.get("pending", 0)),
            "lastVerifyTs": int(watch.get("lastVerifyTs") or 0) or None,
        },
    }


def degrade_wire(code: str, *, retry_in_s: int | None = None, **fields) -> dict:
    """降级码 → wire（文案**由后端下发**，前端不二次编，§3.7.4）。"""
    return {"code": code, "message": tax.degrade_message(code, **fields),
            "retryInS": retry_in_s}


def estimate_daily_cost(*, symbols: int, interval_min: float, corpus_docs: int,
                        snippet_chars: int, verify_enabled: bool = False,
                        verify_docs: int = 0) -> dict:
    """设置卡片上的成本估算（R-4：让「一天大概多少 token」在按下开关**之前**就看得见）。

    Args:
        verify_enabled / verify_docs: 核查轮的开关与单轮文档数（closed-loop §3.5）。
            两个 keyword-only 形参默认为「不算核查轮」⇒ 既有调用点逐字节回退。
            **核查轮必须被算进去**：日配额被三种轮共享是刻意的，把它悄悄扩大一倍不是一个可以
            由实现自行决定的事。
    """
    rounds = max(0.0, symbols * (1440.0 / max(1.0, interval_min)))
    per_round_chars = corpus_docs * snippet_chars + 3000
    total = rounds * per_round_chars
    if verify_enabled and verify_docs > 0:
        # 核查轮挂在整理轮之前，节律≈整理轮（默认每 4 次采集一次），语料是 verify_docs 篇。
        verify_rounds = rounds / 4.0
        total += verify_rounds * (verify_docs * snippet_chars + 2000)
        rounds += verify_rounds
    return {"roundsPerDay": int(math.ceil(rounds)),
            "inputTokensPerDay": int(total / 2.5)}


# ---------------- 设置解析（DB 行优先、env 回落，与 `resolve_active_provider` 同构） ----------------
# `(wire 键, app_settings 键, Settings 字段, 类型)`。**单一真源**：读端点、写端点与运行时装配点
# 共用它，避免「面板上改得动、系统永远不读、而且不报错」这类假面板（experience 的 I-10 教训）。
INTEL_SETTING_BRIDGE: tuple[tuple[str, str, str, type], ...] = (
    ("enabled", "ai_intel.enabled", "ai_trade_intel_enabled", bool),
    ("injectEnabled", "ai_intel.inject", "ai_trade_intel_inject", bool),
    ("provider", "ai_intel.provider", "ai_trade_intel_search_provider", str),
    ("collectIntervalMin", "ai_intel.collect_interval_min",
     "ai_trade_intel_collect_interval_min", float),
    ("curateEveryNCollects", "ai_intel.curate_every_n",
     "ai_trade_intel_curate_every_n_collects", int),
    ("dailyCap", "ai_intel.daily_cap", "ai_trade_intel_daily_round_cap", int),
    # ---- closed-loop 评审 P0-5：三把安全单键 + 五个核查旋钮**必须**扩这张表。
    # 不扩 ⇒ 请求 200、面板上开关照动、`Settings` 上的值**永远不变**——用户在设置卡上关掉核查轮，
    # 系统照跑照降级。把安全开关做成假开关是本轮最不可接受的一种失败（G-18 逐键往返冻结）。
    ("citeEnabled", "ai_intel.cite_enabled", "ai_trade_intel_cite_enabled", bool),
    ("attributionEnabled", "ai_intel.attribution_enabled",
     "ai_trade_intel_attribution_enabled", bool),
    ("verifyEnabled", "ai_intel.verify_enabled", "ai_trade_intel_verify_enabled", bool),
    ("verifyEveryNCurates", "ai_intel.verify_every_n",
     "ai_trade_intel_verify_every_n_curates", int),
    ("verifyMaxItems", "ai_intel.verify_max_items", "ai_trade_intel_verify_max_items", int),
    ("verifyDocsPerItem", "ai_intel.verify_docs_per_item",
     "ai_trade_intel_verify_docs_per_item", int),
    ("blockedSweepEnabled", "ai_intel.blocked_sweep",
     "ai_trade_intel_blocked_sweep_enabled", bool),
    ("citeMaxRefs", "ai_intel.cite_max_refs", "ai_trade_intel_cite_max_refs", int),
)
# 密钥的 app_settings 键。**绝不出接口**（`GET /settings/intel` 只回 `hasKey` / `keyPreview`）、
# **绝不进日志**（`IntelSearchError` 构造时即脱敏）。
INTEL_API_KEY_APP_KEY = "ai_intel.search_api_key"


async def resolve_intel_settings(session, settings):
    """把库内行叠加到 `Settings` 上，产出**运行时真正会读到的那一份**。

    产出会话私有副本而不是改全局：`get_settings()` 是进程级 `lru_cache` 单例，且 `LiveExecutor`
    被 `ai_auto` 与 `live_auto` 共用同一个 settings 对象，`setattr` 会让情报设置静默溢出到非 AI
    链路（本仓库的历史坑）。
    """
    updates: dict = {}
    for _wire_key, app_key, field_name, caster in INTEL_SETTING_BRIDGE:
        stored = await repo.get_app_setting(session, app_key, None)
        if stored is None:
            continue
        try:
            updates[field_name] = bool(stored) if caster is bool else caster(stored)
        except (TypeError, ValueError):
            continue
    stored_key = await repo.get_app_setting(session, INTEL_API_KEY_APP_KEY, None)
    if isinstance(stored_key, str) and stored_key:
        updates["ai_trade_intel_search_api_key"] = stored_key
    return settings.model_copy(update=updates) if updates else settings


async def resolve_api_key(session, settings) -> str:
    """检索源密钥：库内行优先、env 回落（§3.2.3 的解析优先级）。"""
    stored = await repo.get_app_setting(session, INTEL_API_KEY_APP_KEY, None)
    if isinstance(stored, str) and stored.strip():
        return stored.strip()
    return str(getattr(settings, "ai_trade_intel_search_api_key", "") or "").strip()
