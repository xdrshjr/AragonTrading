"""生成层编排（strategy-factor-discovery §2.5 / 单轮第 ① 步）—— 五维演化的候选产出。

自 ``runtime.py`` 拆出（该文件超 800 行清洁度上限）。本模块**只**负责「按预算把名额切给五种
操作，并把每个名额换成一个合法候选」，不碰持久化、不碰事件、不碰状态机——那些留在 runtime。

两条纪律：
- **绝不静默空转**：某个操作因父本不足 / 搜索器失败而产不出候选时，名额回落到随机探索，
  而不是让这一格白白蒸发（否则 UI 上「生成 40」实际只有 12 条，用户无从察觉）。
- **LLM 份额合并成一次调用**，降级即把份额转给随机（fail-open，§2.11）。
"""
from __future__ import annotations

from .evolution import SearchContext, allocate_operations, build_search_context, tournament_select
from .expression import parse_expression
from .expression_text import describe_expression
from .fsa import blacklisted_skeletons
from .genomes import Candidate, ExprGenome, make_factor_candidate
from .llm_agents import LlmOutcome
from .mechanisms import uncovered_families

MAX_RECENT_KEPT = 8
MAX_HINTS = 6


class CandidateFactory:
    """把「预算 + 已入库池 + 机制覆盖」变成一批候选（一轮一次，无跨轮状态）。"""

    def __init__(self, searcher, llm, momentum):
        self._searcher = searcher
        self._llm = llm
        self._momentum = momentum

    async def produce(self, rng, *, budget: dict, count: int, pool: list,
                      coverage: dict, bans: list, skeleton_stats: dict,
                      tally) -> tuple[list[Candidate], LlmOutcome]:
        """产出本轮全部候选；返回 ``(候选列表, LLM 调用产物)``。"""
        plan = allocate_operations(budget, count, rng)
        ctx = build_search_context(_pool_nodes(pool), mechanism_hint=_hint(rng, coverage))
        produced: list[Candidate] = []
        for source in plan:
            if source == "llm":
                continue
            genome = self._apply(source, rng, ctx, pool)
            if genome is not None:
                produced.append(make_factor_candidate(genome.node, source))
        outcome, from_llm = await self._via_llm(
            plan.count("llm"), rng, ctx, pool=pool, coverage=coverage, bans=bans,
            skeleton_stats=skeleton_stats)
        produced.extend(from_llm)
        tally.generated = len(produced)
        for candidate in produced:
            tally.count_source(candidate.source)
        return produced, outcome

    def _apply(self, source: str, rng, ctx: SearchContext, pool: list) -> ExprGenome | None:
        """把一个操作名映射到搜索器调用；父本不足即回落 ``seed``（名额绝不蒸发）。"""
        if source in ("mutate", "perturb", "crossover") and not pool:
            return self._searcher.seed(rng, ctx)
        if source == "mutate":
            parent = _parent(rng, pool)
            return self._searcher.mutate(rng, parent, ctx) if parent else None
        if source == "perturb":
            parent = _parent(rng, pool)
            return (self._searcher.perturb(rng, parent, ctx, self._momentum)
                    if parent else None)
        if source == "crossover":
            first, second = _parent(rng, pool), _parent(rng, pool)
            return (self._searcher.crossover(rng, first, second, ctx)
                    if first and second else None)
        return self._searcher.seed(rng, ctx)

    async def _via_llm(self, slots: int, rng, ctx: SearchContext, *, pool: list,
                       coverage: dict, bans: list,
                       skeleton_stats: dict) -> tuple[LlmOutcome, list[Candidate]]:
        """LLM 份额合并成**一次**调用；解析不出足够条数即用随机探索补齐（fail-open）。"""
        if slots <= 0:
            return LlmOutcome(), []
        if not self._llm.enabled:
            return (LlmOutcome(degraded=True, degraded_reason="not_configured"),
                    self._fallback(slots, rng, ctx))
        outcome = await self._llm.generate({
            "count": slots,
            "mechanismCoverage": coverage,
            "uncoveredFamilies": uncovered_families(coverage),
            "recentKept": _recent_kept(pool),
            "bannedSkeletons": [ban.skeleton for ban in bans[:MAX_HINTS]],
            "failurePatterns": sorted(blacklisted_skeletons(skeleton_stats))[:MAX_HINTS]})
        produced: list[Candidate] = []
        for item in outcome.payload.get("candidates") or []:
            genome = self._searcher.from_llm(item, ctx)
            if genome is not None:
                produced.append(make_factor_candidate(genome.node, "llm"))
        missing = slots - len(produced)
        if missing > 0:
            produced.extend(self._fallback(missing, rng, ctx))
        return outcome, produced

    def _fallback(self, count: int, rng, ctx: SearchContext) -> list[Candidate]:
        out: list[Candidate] = []
        for _ in range(max(0, count)):
            genome = self._searcher.seed(rng, ctx)
            if genome is not None:
                out.append(make_factor_candidate(genome.node, "random"))
        return out


def _pool_nodes(pool: list) -> list:
    """已入库池 → 表达式树列表（供数据驱动分布统计）；陈旧条目静默跳过，不拖垮生成。"""
    nodes = []
    for entry in pool:
        try:
            nodes.append(parse_expression(entry.expr_text))
        except Exception:  # noqa: BLE001
            continue
    return nodes


def _parent(rng, pool: list) -> ExprGenome | None:
    """锦标赛选出一个父本并解析成基因（解析失败返回 None，由调用方跳过该名额）。"""
    entry = tournament_select(rng, pool, size=3)
    if entry is None:
        return None
    try:
        return ExprGenome(parse_expression(entry.expr_text))
    except Exception:  # noqa: BLE001
        return None


def _hint(rng, coverage: dict) -> str:
    """机制引导提示：优先补一个 0 覆盖的族（全覆盖时不提示，让分布自由发挥）。"""
    missing = uncovered_families(coverage)
    return missing[rng.randrange(len(missing))] if missing else ""


def _recent_kept(pool: list) -> list[dict]:
    """近期入库因子摘要（进生成 prompt 的「勿重复其结构」清单）。"""
    return [{"expr": entry.expr_text, "describe": describe_expression(entry.expr_text),
             "mechanism": entry.mechanism} for entry in pool[-MAX_RECENT_KEPT:]]
