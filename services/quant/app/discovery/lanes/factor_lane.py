"""因子轴 Lane（discovery-strategy-axes §2.2）—— 从 ``runtime.py`` **逐行平移**。

⚠️ **BL-8 承重**：本文件的每一行都必须与拆分前的 ``runtime`` 实现语义逐位相同。
``tests/test_discovery_factor_lane_golden.py`` 是它的机器判据——任何差异都视为回归，
不接受「新实现更合理」的解释。要改因子轴行为，请另开一轮并先改 golden 常量。
"""
from __future__ import annotations

from ..evolution import MomentumTracker
from ..expression import evaluate, parse_expression
from ..fsa import active_bans
from ..generation import CandidateFactory
from ..review import ReviewContext, filter_rules, review_values
from ..searchers import FactorSearcher
from ..validation import (
    KeptEntry,
    ValidationContext,
    ValidationOutcome,
    _context_of,
    normalize_thresholds,
    validate_candidate,
)
from .base import LaneContext, trim_pool

DEFAULT_FEE_BPS = 5.0
DEFAULT_SLIP_BPS = 2.0


class FactorLane:
    """``factor`` 轴：表达式树搜索 + 11 条联合过滤 + 值级去重池。"""

    kind = "factor"

    def __init__(self, *, config: dict, momentum: MomentumTracker, llm,
                 coverage: dict) -> None:
        self.config = config
        self.coverage = coverage
        self.pool: list[KeptEntry] = []
        self.tested = 0
        self.tested_hashes: set[str] = set()
        self.skeleton_stats: dict = {}
        self.bans: list = []
        self.iteration = 0
        self._momentum = momentum
        self._llm = llm
        self._searcher = FactorSearcher()
        self._factory = CandidateFactory(self._searcher, llm, momentum)

    # ---------- 状态 ----------
    def mark_tested(self, genome_hash: str) -> None:
        self.tested_hashes.add(genome_hash)

    def on_data_refresh(self) -> None:
        """数据窗右移后重建生成器（与拆分前 ``_refresh_data`` 的动作逐字相同）。"""
        self._factory = CandidateFactory(self._searcher, self._llm, self._momentum)

    def pool_size(self) -> int:
        return len(self.pool)

    def genome_payload(self, candidate) -> dict:
        return {"expr": candidate.expr_text}

    def snapshot(self) -> dict:
        return {"pool": len(self.pool), "tested": int(self.tested),
                "bans": len(self.bans), "skeletons": len(self.skeleton_stats)}

    # ---------- ① 生成 ----------
    async def produce(self, rng, *, budget: dict, count: int, tally):
        return await self._factory.produce(
            rng, budget=budget, count=count, pool=self.pool, coverage=self.coverage,
            bans=self.bans, skeleton_stats=self.skeleton_stats, tally=tally)

    # ---------- ② 审查 ----------
    def review(self, candidates: list):
        return filter_rules(candidates, self.review_ctx())

    def review_ctx(self) -> ReviewContext:
        evolution = self.config.get("evolution") or {}
        kept_counts: dict[str, int] = {}
        for entry in self.pool:
            kept_counts[entry.skeleton] = kept_counts.get(entry.skeleton, 0) + 1
        return ReviewContext(
            tested_hashes=self.tested_hashes,
            banned_skeletons=active_bans(
                self.bans, iteration=int(self.iteration),
                ttl_iters=int(evolution.get("fsaBanTtlIters", 150))),
            kept_skeleton_counts=kept_counts,
            skeleton_stats=self.skeleton_stats,
            variant_cap=int(evolution.get("variantCap", 6)),
            min_coverage=float(normalize_thresholds(
                self.config.get("thresholds"))["minCoverage"]))

    # ---------- ③ 验证 ----------
    def validation_ctx(self, ctx: LaneContext) -> ValidationContext:
        settings = ctx.settings
        return ValidationContext(
            df_full=ctx.df_full, horizon=int(self.config.get("horizon", 6)),
            is_ratio=float(self.config.get("isRatio", 0.7)),
            fee_bps=float(getattr(settings, "default_taker_fee_bps", DEFAULT_FEE_BPS)),
            slip_bps=float(getattr(settings, "default_slippage_bps", DEFAULT_SLIP_BPS)),
            thresholds=normalize_thresholds(self.config.get("thresholds")), memo=ctx.memo)

    def validate(self, candidate, ctx: LaneContext, trials: int) -> ValidationOutcome:
        """先跑末位的 ``constant_or_empty``（共用记忆化求值），再跑 11 条闸。"""
        vctx = self.validation_ctx(ctx)
        values = evaluate(candidate.genome.node, _context_of(vctx), vctx.memo)
        reason = review_values(values, self.review_ctx())
        if reason:
            return ValidationOutcome(verdict="rejected", reject_reason=reason,
                                     direction="long", metrics={}, series={})
        return validate_candidate(candidate.genome.node, vctx, pool=self.pool, trials=trials)

    # ---------- 池 ----------
    def absorb(self, candidate, outcome, tally) -> None:
        """kept 立刻进池，使**同轮**后续候选就能与它做相关性去重（防同轮双胞胎双双入库）。"""
        if outcome.verdict != "kept" or outcome.values is None:
            return
        tally.kept += 1
        self.coverage[candidate.mechanism] = int(self.coverage.get(candidate.mechanism, 0)) + 1
        self.pool.append(KeptEntry(
            candidate_id=0, expr_text=candidate.expr_text, direction=outcome.direction,
            values=outcome.values, ic_series=outcome.ic_series, score=outcome.score,
            mechanism=candidate.mechanism, skeleton=candidate.skeleton))
        trim_pool(self.pool, self._max_kept())

    def _max_kept(self) -> int:
        return int(self.config.get("maxKeptPerAxis") or self.config.get("maxKept", 200))

    def adopt(self, entries: list) -> None:
        self.pool = list(entries)

    def parent_genomes(self) -> list:
        """供跨轴/上游取材（因子轴自身不需要，返回表达式文本即可）。"""
        return [(entry.expr_text, entry.direction) for entry in self.pool]

    def parse_expr(self, text: str):
        return parse_expression(text)
