"""采集轮与整理轮的完整编排（ai-trade-market-intel §3.3 / §3.4）。

**不变式 I-4（会话边界，评审 P1-8）：任何 DB 会话都不得跨越一次 LLM 或检索的 `await`。**
SQLite 是单写者——一个横跨 60 秒 LLM 调用的写事务会把**全库**其它写入（成交落库、run metrics、
经验层物化）一起阻塞数十秒。落地形态：

- 采集轮：步骤 ① 开一个**只读**短会话取 watch 行与库存后立即关闭；步骤 ②–⑦ **不持有跨网络
  `await` 的会话**（检索、抓取与 LLM 都在这一段；③·5 的两个**毫秒级**短会话开在两次网络
  `await` 之间）；步骤 ⑧–⑨ 再开一个写会话，`upsert_items` + `write_quotes` +
  `write_quote_state` + `write_round` + `bump_watch` **一次提交**。
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
from . import alert as alert_mod
from . import arming
from . import conflict as conflict_mod
from . import efficacy as efficacy_mod
from . import corpus as corpus_mod
from . import gate, prompts
from . import evidence as evidence_mod
from . import quotes as quotes_mod
from .agent import run_round
from .disclosure import build_digest_and_ids
from .gate import IntelItem, IntelSource
from .search import IntelSearchError, SearchRunner
from .session_rule import IntelSessionRule
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
        verify_count=int(wire.get("verifyCount") or 0),
        # ---- evidence-grounding §5.3.1 R-b：**读出通路的第二跳** ----
        # 漏了 `scheduleResolvedTs` 这一行，`plan_cleanup` 的新判据 `schedule_resolved_ts == 0`
        # **恒真** ⇒ F-1 从「被核查过就永不降层」翻成「兑现了也照样降层」，方向相反、同样静默。
        quoted=bool(wire.get("quoted")),
        quote_count=int(wire.get("quoteCount") or 0),
        quote_state=wire.get("quoteState", "") or "",
        schedule_resolved_ts=int(wire.get("scheduleResolvedTs") or 0))


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
    # evidence-grounding 轴 A：**只持配置**的抓取器（`ReaderConfig` 是 frozen dataclass，
    # `httpx.AsyncClient` 在 `fetch_many` 内部 `async with` 建与关）。``None`` ⇒ **不抓、不报错**，
    # 语料块逐字节回到本轮合入前。这也是「不新增关停步骤」唯一能成立的前提。
    reader: object | None = None
    group_id: str = ""
    bar: str = "15m"
    change_24h_pct: Callable[[str], float | None] | None = None
    now: Callable[[], float] = time.time
    limits: IntelLimits = field(default=None)   # type: ignore[assignment]
    # session-binding 轴 A：一次会话对情报层的采信约定。**带默认值 ⇒ 既有 30+ 个直接构造 deps
    # 的离线用例一个字节不改**，且默认值（enabled=True, mode="full"）就是本特性合入前的行为。
    #
    # ⚠️ 会话选择**绝不能**通过在 `Settings` 副本上改 `ai_trade_intel_inject` 来表达：
    # `build_group_runtime` 的第一件事就是 `resolve_intel_settings(db, settings)`，而
    # `INTEL_SETTING_BRIDGE` 里恰好有 `("injectEnabled", "ai_intel.inject", …)`——只要用户在
    # 设置中心动过一次那个开关，库内行就会**静默覆盖**会话的选择，而且不报错。故它必须是一个
    # 独立的、显式的字段，在 `resolve_intel_settings` **之后**才与运维总闸合取。
    session_rule: IntelSessionRule = IntelSessionRule()

    @property
    def inject_on(self) -> bool:
        """情报是否进入本会话的决策上下文（**全仓唯一判据**）。

        原先散在 `build_snapshot` 里的那一行 `getattr(deps.settings, "ai_trade_intel_inject",
        True)` 由本属性取代——两处各判一次必然分叉，而分叉不报错。

        ⚠️ **本属性不含 `armed`**：`armed=False` 时 `IntelRuntime` 是一个壳（`refresh` 早退、
        `_digest` 恒空），第一项天然生效，判两次就是第二个真源。「此刻真的在喂吗」是另一个
        谓词（`IntelBinding.injecting`），两者不许互相冒充（评审 P1-6）。
        """
        if not bool(getattr(self.settings, "ai_trade_intel_inject", True)):
            return False                       # 运维总闸优先级最高
        return bool(self.session_rule.enabled)

    @property
    def intel_mode(self) -> str:
        """本会话的生效档：`off` | `index` | `full`（`build_snapshot` 据它选 renderer）。"""
        return self.session_rule.effective_mode if self.inject_on else "off"

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
    # actionability 轴 A：本轮判定出来的待处理告警（`fired=False` ⇒ 恒为 None）。
    # **带默认值的第十个字段** ⇒ 既有九个构造点一个字节不改。`IntelRuntime._after_round` 在
    # `refresh` **之后**把它交给会话（顺序不可换：`ref` / `in_digest` 要从刚刷新的 digest 反查）。
    alert: alert_mod.AlertHandoff | None = None


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

    # ②–⑦ 本段**不持有跨网络 `await` 的会话**（不变式 I-4）：检索、抓取与 LLM 都在这一段，
    # 而 ③·5 的两个毫秒级短会话开在两次网络 `await` **之间**。
    queries = corpus_mod.plan_queries(ctx, count=int(deps.settings.ai_trade_intel_queries_per_round))
    built, failed = await _gather_corpus(deps, inst_id, ctx, blocked=blocked, queries=queries,
                                         started=started, trusted=trusted)
    if built is None:
        return failed
    # ③·5 / ③·6：抓正文 → 挂进语料（**整体重建** docs / block / index）。
    built, reader_counts = await evidence_mod.attach_document_bodies(deps, ctx, built)
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
                                     doc_count=len(built.docs), corpus=built, usage=result,
                                     reader_counts=reader_counts)
    proposals = list(result.raw.get("items") or [])
    admitted = gate.admit(proposals, built, existing,
                          inst_id=inst_id, limits=deps.limits, now=now,
                          trusted_domains=trusted)
    return await _persist_collect(deps, inst_id, started=started, queries=queries,
                                  corpus=built, admitted=admitted, usage=result,
                                  reader_counts=reader_counts,
                                  quotes_proposed=quotes_mod.count_proposed(proposals),
                                  # actionability 评审 P1-4：**merges 分支的全部内容字段只能
                                  # 从这张表出**——`MergeOp` 一个内容字段都没有，而「同一条
                                  # 风险情报被第二个域名再次报道」恰恰只走那条分支。
                                  existing=existing)


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
                           queries: list[str], corpus, admitted, usage,
                           reader_counts: dict, quotes_proposed: int,
                           existing: dict | None = None) -> RoundOutcome:
    """采集轮的步骤 ⑧–⑨：落条目 + 落引文 + 落告警台账 + 落轮 + 推节律，**一次提交**。

    ★ actionability 评审 P1-5：**告警判定必须落在这里，不是 `run_collect_round` 的函数体**。
    三条理由缺一不可：① `item_id` 只有 `upsert_intel_items` 的返回值知道，出了这个 `async with`
    就再也拿不到；② 冷却时刻与日配额的两条 SELECT 也要一个会话，而这一个是本轮唯一开着的写
    会话，另开一个就是 SQLite 单写者下的嵌套会话；③ 台账行必须与条目行**同生共死**——条目
    回滚而告警行留下，等于凭空多出一条指向不存在条目的告警。
    """
    status = "ok" if (admitted.accepted or admitted.merges) else "degraded"
    degrade_code = "" if status == "ok" else "all_rejected"
    verified_total = sum(len(v) for v in admitted.quotes.values())
    async with deps.sessionmaker()() as db:
        round_id = await repo.write_intel_round(db, {
            "instId": inst_id, "kind": "collect", "groupId": deps.group_id,
            "startedTs": started, "finishedTs": int(deps.now()), "status": status,
            "searchProvider": deps.search.slug if deps.search else "", "queries": queries,
            "docCount": len(corpus.docs), "domainCount": corpus.domain_count,
            "accepted": len(admitted.accepted), "rejected": admitted.total_rejected,
            "merged": len(admitted.merges), "rejects": admitted.rejects,
            "inputTokens": usage.input_tokens, "outputTokens": usage.output_tokens,
            "degradeCode": degrade_code,
            "readerAttempt": int(reader_counts.get("readerAttempt", 0)),
            "readerOk": int(reader_counts.get("readerOk", 0)),
            "quotesProposed": int(quotes_proposed),
            "quotesVerified": verified_total}, commit=False)
        item_ids: list[int] = []
        if admitted.accepted:
            # ★ 必须**接住**返回值：它是 accepted 的引文找到自己 `item_id` 的唯一途径
            # （`upsert_intel_items` 按入参顺序返回）。
            item_ids = await repo.upsert_intel_items(
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
        quote_rows, states = evidence_mod.plan_quote_writes(
            inst_id, round_id, admitted, item_ids, now=int(deps.now()))
        await repo.write_intel_quotes(db, quote_rows, commit=False)
        await repo.write_intel_quote_state(db, states, commit=False)
        await repo.bump_intel_watch(
            db, inst_id, kind="collect", status=status,
            next_due_ts=next_due_for(deps, fail_streak=0), commit=False)
        # 投影在**这里**做而不是在 `_persist_alert` 里：`admitted` / `existing` /
        # `item_ids` 三样只有这一处同时持有，而把它们一路带进去会让那个函数到 7 个形参
        # （越 CLAUDE.md 的 5 个上限）。`candidates_from_round` 是纯函数，放在写会话里
        # 不额外开销。
        handoff = await _persist_alert(
            deps, db, inst_id, round_id=round_id,
            candidates=alert_mod.candidates_from_round(admitted, existing or {},
                                                       item_ids=item_ids))
        await db.commit()
    return RoundOutcome(inst_id, "collect", status, degrade_code, len(admitted.accepted),
                        len(admitted.merges), admitted.total_rejected, alert=handoff)


async def _persist_alert(deps: IntelDeps, db, inst_id: str, *, round_id: int,
                         candidates: list):
    """算一次告警判定并落一行台账（**同一个会话、同一次 commit**）。

    **无论 fired 与否都写**——「今天为什么一次都没告警」的答案就住在这些行里，空轮同理。

    Args:
        deps: 依赖聚合（取 `limits` 与 `now`）。
        db: `_persist_collect` 持有的那个写会话（**同一个**，见该函数的方框）。
        inst_id: 目标标的。
        round_id: 产生本次判定的采集轮。
        candidates: `alert.candidates_from_round` 的产物（**投影在调用点做**：只有那里
            同时持有 `admitted` / `existing` / `item_ids` 三样）。

    Returns:
        `alert.AlertHandoff`（`fired=True`）或 ``None``。`ref` 与 `in_digest` 在这里**故意
        留空**：它们要从 `IntelRuntime._after_round` 里那份**刚刷新完**的 digest 反查
        （评审 P0-3），此刻的 digest 还是上一轮的。
    """
    now = deps.now()
    state = await repo.alert_cooldown_state(db, inst_id, now=now)
    decision = alert_mod.plan_alert(candidates, limits=deps.limits, now=now,
                                    last_fired_ts=int(state["lastFiredTs"]),
                                    day_fired=int(state["dayFired"]))
    candidate = decision.candidate
    alert_id = await repo.write_intel_alert(db, {
        "instId": inst_id, "groupId": deps.group_id,
        "itemId": candidate.item_id if candidate else 0,
        "roundId": round_id, "createdTs": int(now),
        "severity": decision.severity, "fired": decision.fired,
        "suppressedCode": decision.suppressed_code,
        "headline": candidate.headline if candidate else "",
        "category": candidate.category if candidate else "",
        "tier": candidate.tier if candidate else "",
        "stance": candidate.stance if candidate else "",
        "impact": candidate.impact if candidate else "",
        "domainCount": candidate.domain_count if candidate else 0,
        "quoted": candidate.quoted if candidate else False,
        "confidence": candidate.confidence if candidate else 0.0,
        "reasons": decision.reasons}, commit=False)
    if not decision.fired or candidate is None:
        return None
    return alert_mod.AlertHandoff(alert_id=alert_id, candidate=candidate,
                                  severity=decision.severity, created_wall=now)


# 战绩求解一次读多少行引用明细。行数本来就被 `cite_retention_d`（默认 180 天）天然封顶，
# 这个上界只是为了让「一个跑了三年的标的」不会在一次快照里把整张表读进内存。
# **两个求解点必须用同一个数**——否则 G-28 的「两处逐字段相等」会在库大了之后悄悄变假。
_EFFICACY_MAX_ROWS = 2000
# 推算 `openRounds` 时回看多少次整理轮。超过它就说「至少这么多轮」——一个悬置了 40 轮的矛盾对
# 与一个悬置了 200 轮的，在界面上是同一句话。
_CURATE_ROUND_LOOKBACK = 40


async def solve_efficacy(db, deps: IntelDeps, inst_id: str, scope: list[dict]):
    """求解该标的的战绩与信誉先验（**两个求解点共用这一个入口**）。

    * actionability 评审 P0-1：`build_snapshot` 与 `run_curate_round` **各求一次**，这不构成
    第二个真源——`efficacy.solve` 是纯函数，同一批 ``(citation_rows, item_rows, limits, now)``
    在哪里求都得到同一个值，而两处的输入来自同一张表、同一个标的、相隔至多一个 tick
    （G-28 冻结这条）。整理轮够不着 `IntelRuntime._efficacy` 那份缓存：`IntelDeps` **没有任何
    指向 runtime 的引用**，它被刻意设计成「显式注入，使离线用例不必造一个真的会话」。

    Args:
        db: 调用方持有的只读会话。
        deps: 依赖聚合（取 `limits` 与 `now`）。
        inst_id: 目标标的。
        scope: `load_intel_scope` 的 wire 行（**复用调用方已经取好的那一份**，不再查一次）。

    Returns:
        `efficacy.Efficacy`；总闸关 ⇒ 空产物（全部权重为 0 ⇒ 四个消费点逐字节回退）。
    """
    if not deps.limits.efficacy_enabled:
        return efficacy_mod.Efficacy()
    rows, _has_more = await repo.list_intel_citations(db, inst_id=inst_id,
                                                      limit=_EFFICACY_MAX_ROWS)
    return efficacy_mod.solve(rows, scope, limits=deps.limits, now=deps.now())


async def read_efficacy(db, inst_id: str, *, limits: IntelLimits, now: float):
    """REST 侧的战绩求解入口（**与 `solve_efficacy` 同一批入参、同一个纯函数**）。

    端点层拿不到 `IntelDeps`（它是情报后台任务的依赖聚合，而 REST 请求手上只有一个会话与
    `Settings`），故这里自己取一次 scope。**这不构成第三个真源**：`efficacy.solve` 是纯函数，
    同一批 ``(citation_rows, item_rows, limits, now)`` 在哪里求都得到同一个值（G-28 的性质）。
    """
    if not limits.efficacy_enabled:
        return efficacy_mod.Efficacy()
    scope = await repo.load_intel_scope(db, inst_id)
    rows, _has_more = await repo.list_intel_citations(db, inst_id=inst_id,
                                                      limit=_EFFICACY_MAX_ROWS)
    return efficacy_mod.solve(rows, scope, limits=limits, now=now)


def efficacy_wire(eff, *, limits: IntelLimits) -> dict:
    """`GET /impact` 的 `efficacy` 块（**唯一**装配点；`IntelEfficacyOut` 与它逐键同构）。"""
    rows = [{**efficacy_mod.stat_wire(stat), "category": name,
             "weight": round(float(eff.weights.get(name, 0.0)), 6)}
            for name, stat in sorted(eff.by_category.items())]
    return {"enabled": bool(limits.efficacy_enabled),
            "kMin": int(limits.efficacy_k_min),
            "sampleN": int(eff.overall.samples),
            "overall": efficacy_mod.stat_wire(eff.overall),
            "byCategory": rows}


def source_reputation_wire(eff, *, limits: IntelLimits) -> dict[str, dict]:
    """域名 → 三个判定计数 + 准确率（信源页签的三列）。

    **样本不足时 `accuracy` 恒为 `None`**（低于 `efficacy_min_domain_samples`）：一个用两条样本
    算出来的 100% 准确率，比不显示准确率更容易让人拉黑错的那个源——而拉黑是这一页上唯一的
    破坏性动作。
    """
    floor = max(1, int(limits.efficacy_min_domain_samples))
    out: dict[str, dict] = {}
    for domain, stat in eff.by_domain.items():
        wire = efficacy_mod.stat_wire(stat)
        if stat.samples < floor:
            wire["accuracy"] = None
        out[domain] = wire
    return out


async def load_conflicts(db, inst_id: str, *, limits: IntelLimits, now: float,
                         items: list[IntelItem] | None = None):
    """找出该标的库存里的立场矛盾对，并算出每一对悬置了几轮整理。

    **注入端、整理轮与 REST 三处共用这一个入口**：`find_conflicts` 是纯函数，三处同值。

    Args:
        db: 只读会话（只用来取整理轮流水，`openRounds` 由它推算）。
        inst_id: 目标标的。
        limits: 提供 `conflict_enabled` 与 `conflict_min_overlap`（纯核心不读 `Settings`）。
        now: 墙钟秒。
        items: 已在手的库存；``None`` 时本函数自己取一次。

    Returns:
        ``(pairs, wire_rows)``；总闸关 ⇒ ``((), [])`` ⇒ 注入端与 REST 双双逐字节回退。
        `wire_rows` 按「悬置轮数降序、重叠度降序」——久未裁决的对必须排在下一轮整理的最前面。
    """
    if not limits.conflict_enabled:
        return (), []
    rows = items if items is not None else [
        item_from_wire(w) for w in await repo.load_intel_scope(db, inst_id)]
    pairs = tuple(conflict_mod.find_conflicts(
        rows, min_overlap=limits.conflict_min_overlap,
        max_pairs=conflict_mod.MAX_CONFLICT_PAIRS, now=now))
    if not pairs:
        return (), []
    by_id = {int(it.item_id): it for it in rows if it.item_id}
    curate_ts = [int(row.get("startedTs") or 0) for row in await repo.list_intel_rounds(
        db, inst_id, kind="curate", limit=_CURATE_ROUND_LOOKBACK)]
    wire = [pair.wire(open_rounds=conflict_mod.open_rounds_for(
        pair, by_id, curate_round_ts=curate_ts)) for pair in pairs]
    wire.sort(key=lambda row: (-row["openRounds"], -row["overlap"]))
    return pairs, wire


# ---------------- 整理轮（**已外移**到 `intel/service_curate.py`） ----------------
# 本文件在 actionability 轮实测 919 行，越过 CLAUDE.md 的 800 行阈值，处置是「再下沉一层」而
# 不是放宽阈值（§十二 / `EG-I2` 的成文修法：纯搬运、零语义变化）。文件名沿用同目录里
# `service_verify.py` 的既有范式。


async def run_curate_round(deps: IntelDeps, inst_id: str) -> RoundOutcome:
    """跑一轮整理（**薄转发**，判定与编排全在 `service_curate`）。

    延迟导入是必需的而不是风格选择：`service_curate` 反向依赖本模块的公开面
    （`IntelDeps` / `solve_efficacy` / `load_conflicts` / `finish_degraded`），模块级互相
    import 会成环。逐行照抄 `IntelRuntime.run_verify` 对 `service_verify` 的既有做法。
    """
    from .service_curate import run_curate_round as _run_curate_round

    return await _run_curate_round(deps, inst_id)


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
                          corpus=None, usage=None,
                          reader_counts: dict | None = None) -> RoundOutcome:
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
            "degradeCode": code, "errorText": error,
            # evidence-grounding：一次在 LLM 阶段失败的轮，抓取成本**照样已经花掉了**。
            # 不记它，命中率的分母会随降级系统性变小（带默认值 ⇒ 既有全部调用点零改动）。
            "readerAttempt": int((reader_counts or {}).get("readerAttempt", 0)),
            "readerOk": int((reader_counts or {}).get("readerOk", 0))}, commit=False)
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
async def build_snapshot(deps: IntelDeps, inst_id: str, *, arm=None,
                         pin_item_id: int = 0) -> dict:
    """一次性算出注入用的 digest 与界面用的 summary（唯一的查库点）。

    `IntelRuntime` 在每一轮结束、用户经 REST 改动条目后、以及 `POST /seen` 之后调它刷新，
    决策热路径只读缓存（§3.6.1）。

    Args:
        arm: 该分组此刻的 `arming.ArmState`。**带默认值的 keyword-only 形参** ⇒ 既有调用点
            （`build_snapshot(deps, INST)`）一个字节不改，回落 `arm_of_deps(deps)`——它以
            `deps.search is not None` 为最硬证据，故离线用例那条「有 runner 即 armed」的既有
            断言继续成立。生产路径由 `IntelRuntime.refresh` 显式传 `self.arm`。
        pin_item_id: session-binding 轴 B-3：要钉进情报段首位的那一条（告警条目）。
            **默认 0 ⇒ 既有离线调用点逐字节回退**；生产路径由 `IntelRuntime.refresh` 从它
            自己的 `_pinned` 状态里取（**不是**形参——`refresh` 有四个调用点，只在其中一个
            传参等于「任何一次标记已读都会把置顶抹掉」，而唤醒句还在说它在，评审 P1-1）。
    """
    now = deps.now()
    arm = arm if arm is not None else arming.arm_of_deps(deps)
    # **在开会话之前就判**：`inject_on` 为假时下面整份 digest 都会被丢弃，那一次引文查询就是
    # 纯浪费——而本轮把会话级采信的默认值定成了**关闭**，那格恰恰是最常见的一格。
    inject_on = deps.inject_on
    async with deps.sessionmaker()() as db:
        summary = await read_summary(db, inst_id, settings=deps.settings, arm=arm, now=now)
        scope = await repo.load_intel_scope(db, inst_id)
        eff = await solve_efficacy(db, deps, inst_id, scope)
        items = [item_from_wire(w) for w in scope]
        conflicts, conflict_rows = await load_conflicts(db, inst_id, limits=deps.limits,
                                                        now=now, items=items)
        quotes = (await _expand_quotes(db, items, limits=deps.limits)
                  if inject_on else None)
    # ★ closed-loop 评审 P1-15：`cite` **只有这一个取值来源**。写在别处会让 REST 读到的库存与
    # 注入看到的库存分叉，而分叉不报错。
    cite_on = bool(getattr(deps.settings, "ai_trade_intel_cite_enabled", False))
    digest, digest_ids = (
        build_digest_and_ids(items, limits=deps.limits, now=now,
                             last_collect_ts=summary["lastCollectTs"], cite=cite_on,
                             eff=eff if deps.limits.efficacy_enabled else None,
                             conflicts=conflicts, mode=deps.intel_mode,
                             pin_ids=frozenset({int(pin_item_id)} if pin_item_id else ()),
                             quotes=quotes)
        # `inject` 关掉时情报段整段不存在 ⇒ id 集合**必须为空**：`in_digest` 因此恒为 False，
        # 唤醒句走内联态并如实说「注入已关闭」（评审 P0-3 的第二条路径）。
        if inject_on else ({"lines": [], "hidden": 0}, frozenset()))
    # `summary["efficacy"]` **不在这里拼**：它由 `read_summary` 这个唯一装配点给出（见那里的
    # 方框）。在这里再拼一次，就会让 WS 推的那一份与 `GET /intel/{instId}` 的那一份各拼各的。
    return {"digest": digest, "summary": summary, "conflicts": conflict_rows,
            "digestItemIds": digest_ids}


async def _expand_quotes(db, items: list[IntelItem], *,
                         limits: IntelLimits) -> dict[int, str] | None:
    """复核轮展开段要用的「每条 ≤1 段已核实引文」（session-binding 轴 B-4）。

    **一次 `IN` 查询**，且只在本会话真的在采信情报（调用点按 `deps.inject_on` 短路）、L2 总闸
    开着、且库里真的有带引文的条目时才发——任一不成立 ⇒ 返回 ``None`` ⇒ 一次多余的查询都
    没有，`bodies` 里 `quote` 恒为空串，产物逐字节回退。

    刻意在**这里**取而不是在会话侧取：决策热路径查库是本仓库明令禁止的，而展开所需的正文必须
    与 `lines` 在同一次定稿里产生（spec §3.0 原则 3）。

    Args:
        db: `build_snapshot` 已经开着的那个只读会话（**不新开会话**）。
        items: 该标的的全部条目。
        limits: 提供 `expand_enabled`。

    Returns:
        ``item_id → 引文原文``；无候选 ⇒ ``None``。
    """
    if not limits.expand_enabled:
        return None
    ids = [int(it.item_id) for it in items
           if it.item_id and it.status == "active" and it.quoted]
    if not ids:
        return None
    return await repo.load_intel_quote_texts(db, ids)


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


# ---------------- 设置解析（**已外移**到 `intel/settings_bridge.py`） ----------------
# 本文件在 actionability 轮越过 CLAUDE.md 的 800 行阈值，处置是「再下沉一层」而不是放宽阈值
# （§十二 / `EG-I2` 的成文修法：纯搬运、零语义变化）。四个公开名在这里**原样重新导出**，
# 故 `intel_service.INTEL_SETTING_BRIDGE` / `intel_service.resolve_intel_settings` 等既有
# 调用点一个字节不改。
from .settings_bridge import (  # noqa: E402,F401
    INTEL_API_KEY_APP_KEY,
    INTEL_SETTING_BRIDGE,
    resolve_api_key,
    resolve_intel_settings,
)

# ---------------- summary 装配（**已外移**到 `intel/summary.py`） ----------------
# 本文件在 session-binding 轮越过 800 行阈值，处置同上：纯搬运、零语义变化，这里原样重新导出，
# 故 `intel_service.read_summary(...)` 的既有三个调用点一个字节不改。
from .summary import read_summary  # noqa: E402,F401

# ---------------- 降级 wire（**已外移**到 `intel/taxonomy.py`） ----------------
# arm-state 评审 P0-2：`degrade_wire` 留在本文件会让纯核心 `intel/arming.py` 为了拼一个三键字典
# 去 import `service`，与 `service` 反向 import `arming` 模块级成环——先解析 `arming` 的入口会
# `ImportError`，整个后端起不来。搬家方向**单向**、零语义变化，这里原样重新导出，故
# `intel_service.degrade_wire(...)` / `service.degrade_wire(...)` 的既有调用点一个字节不改。
from .taxonomy import degrade_wire  # noqa: E402,F401
