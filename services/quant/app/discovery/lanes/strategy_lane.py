"""三条策略轴共用的 Lane（discovery-strategy-axes §2.2 / §2.5 / §2.6）。

靠 ``kind`` + 一个 ``assembler`` 区分 ``strategy_param`` / ``factor_combo`` /
``strategy_combo``：三者的生成器与装配方式不同，但「生成 → 审查 → 回测双段 → S1–S9 → 进池」
这条流水线完全同构，所以是一个类而不是三个。

**逐轴计数（BL-14）**：``tested`` / ``skeleton_stats`` / ``bans`` / ``pool`` 全部是本对象的
实例状态，绝不与别的轴共享。多重检验校正是「同一族假设里测了多少次」，跨族相加没有意义，
更不该让新轴把已发布的因子轴门槛抬高。
"""
from __future__ import annotations

from ..axis_budget import READINESS_REASON_CN
from ..backtest_lane import (
    FactorComboAssembler,
    ParamAssembler,
    RotationAssembler,
    bar_returns,
    costs_of,
    run_split_backtest,
    run_stress_is,
)
from ..catalogs import SearchInputs, template_family
from ..evolution import allocate_operations, tournament_select
from ..fsa import active_bans
from ..genomes import (
    GENOME_LOADERS,
    make_combo_candidate,
    make_param_candidate,
    make_rotation_candidate,
)
from ..llm_agents import LlmOutcome
from ..review import ReviewContext, filter_rules
from ..searchers import SEARCHERS
from ..strategy_gates import (
    StrategyKeptEntry,
    StrategyKeptRow,
    check_gates,
    check_stress,
    normalize_strategy_thresholds,
    rebuild_strategy_pool,
)
from ..validation import ValidationOutcome
from .base import LaneContext, trim_pool

EQUITY_POINTS_CAP = 120
MIN_ROTATION_POOL = 2
FSA_MIN_TEMPLATE_SHARE = 2.0     # strategy_param 轴的自适应阈值分子（见 §2.6.4 的尾注）


class StrategyLane:
    """一条策略轴的完整闭环（生成 / 审查 / 双段回测验证 / S 闸 / kept 池）。"""

    def __init__(self, *, kind: str, config: dict, momentum, llm,
                 catalog: SearchInputs | None = None) -> None:
        self.kind = str(kind)
        self.config = config
        self.inputs = catalog if catalog is not None else SearchInputs()
        self.pool: list[StrategyKeptEntry] = []
        self.tested = 0
        self.tested_hashes: set[str] = set()
        self.skeleton_stats: dict = {}
        self.bans: list = []
        self.iteration = 0
        self.backtests = 0            # 本轮底层 ``run_backtest`` 调用次数（§六的计数单位）
        self.llm_fallback = 0         # LLM 份额回落随机探索的格数（如实上报，绝不静默）
        self._momentum = momentum
        self._llm = llm
        self._searcher = SEARCHERS[self.kind]()

    # ---------- 状态 ----------
    def mark_tested(self, genome_hash: str) -> None:
        self.tested_hashes.add(genome_hash)

    def on_data_refresh(self) -> None:
        """策略轴的生成器无跨轮缓存，数据刷新只需重建目录（由 runtime 负责）。"""

    def pool_size(self) -> int:
        return len(self.pool)

    def genome_payload(self, candidate) -> dict:
        return candidate.genome.payload()

    def snapshot(self) -> dict:
        return {"pool": len(self.pool), "tested": int(self.tested),
                "bans": len(self.bans), "skeletons": len(self.skeleton_stats),
                "llmFallback": int(self.llm_fallback)}

    def ready(self) -> tuple[bool, str]:
        """就绪判据（§2.8）：库存不足即让渡，绝不留「选了却不产候选」的空转轴（BL-9）。"""
        if self.kind == "strategy_param":
            has_template = any(spec.minable for spec in self.inputs.templates)
            return (True, "") if has_template else (False, "no_minable_template")
        if self.kind == "factor_combo":
            enough = len(self.inputs.members) >= 2
            return (True, "") if enough else (False, "member_catalog_too_small")
        floor = int(self.config.get("minRotationPool", MIN_ROTATION_POOL))
        enough = len(self.inputs.rotation_pool) >= floor
        return (True, "") if enough else (False, "upstream_pool_too_small")

    def ready_reason_cn(self) -> str:
        _ready, reason = self.ready()
        return READINESS_REASON_CN.get(reason, reason)

    def fsa_threshold(self, default: float) -> float:
        """``strategy_param`` 轴的阈值随模板数自适应（§2.6.4 尾注）。

        该轴的骨架词表就是注册表里的原子模板，当前只有 8 个左右。15% 的阈值意味着「任何一个
        模板只要占了入库池的 1/6 就被冻结 150 轮」——在 8 元词表上这几乎必然命中最好的那两三个
        模板，等于把搜索赶去挖已知较差的模板。
        """
        if self.kind != "strategy_param":
            return float(default)
        minable = max(1, sum(1 for spec in self.inputs.templates if spec.minable))
        return max(float(default), FSA_MIN_TEMPLATE_SHARE / minable)

    # ---------- ① 生成 ----------
    async def produce(self, rng, *, budget: dict, count: int, tally):
        """五维演化产出候选。

        本轴**不发起 LLM 生成调用**：``strategy_combo`` 由 D-14 明令禁止（成员必须来自本 run
        kept 池，模型无法凭空引用）；另两轴的 LLM 份额按上游 ``_fallback`` 规则回落随机探索，
        回落格数记入 ``llm_fallback`` 并进迭代记录（见 §十一 的偏离记录）。

        回落掉的名额**必须改标 ``random``**（与 ``generation.CandidateFactory._fallback`` 同源）：
        沿用 ``llm`` 标注会让「来源分布」柱状图显示一段本轮根本没发生过的 LLM 产出，而那张图
        的全部意义正是「实际来源与预算背离时一眼看出谁在空转」。
        """
        produced = []
        for source in allocate_operations(budget, count, rng):
            genome = self._apply(source, rng)
            if source == "llm":
                self.llm_fallback += 1
                source = "random"
            if genome is None:
                continue
            produced.append(self._make_candidate(genome, source))
        tally.generated = len(produced)
        for candidate in produced:
            tally.count_source(candidate.source)
        return produced, LlmOutcome()

    def _apply(self, source: str, rng):
        """操作名 → 搜索器调用；父本不足即回落 ``seed``（名额绝不蒸发）。"""
        if source in ("mutate", "perturb", "crossover") and not self.pool:
            return self._searcher.seed(rng, self.inputs)
        if source == "mutate":
            parent = self._parent(rng)
            return self._searcher.mutate(rng, parent, self.inputs) if parent else None
        if source == "perturb":
            parent = self._parent(rng)
            return (self._searcher.perturb(rng, parent, self.inputs, self._momentum)
                    if parent else None)
        if source == "crossover":
            first, second = self._parent(rng), self._parent(rng)
            return (self._searcher.crossover(rng, first, second, self.inputs)
                    if first and second else None)
        return self._searcher.seed(rng, self.inputs)

    def _parent(self, rng):
        entry = tournament_select(rng, self.pool, size=3)
        if entry is None:
            return None
        try:
            return GENOME_LOADERS[self.kind](entry.genome)
        except Exception:  # noqa: BLE001 —— 陈旧基因静默跳过，不拖垮生成
            return None

    def _make_candidate(self, genome, source: str):
        if self.kind == "strategy_param":
            return make_param_candidate(genome, source,
                                        family=template_family(genome.template_key))
        if self.kind == "factor_combo":
            return make_combo_candidate(genome, source)
        return make_rotation_candidate(genome, source)

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
            kept_skeleton_counts=kept_counts, skeleton_stats=self.skeleton_stats,
            variant_cap=int(evolution.get("variantCap", 6)))

    # ---------- ③ 验证 ----------
    def build_assembler(self, genome, ctx: LaneContext):
        """genome → 可回测对象（挖掘期与入库期用**同一把尺子**，见 §2.5.4）。"""
        return assembler_for(self.kind, genome, ctx.settings)

    def thresholds(self) -> dict:
        return normalize_strategy_thresholds(self.config.get("strategyThresholds"))

    def validate(self, candidate, ctx: LaneContext, trials: int) -> ValidationOutcome:
        """双段回测 → S1–S8 →（幸存者才付）S9 成本压力。"""
        assembler = self.build_assembler(candidate.genome, ctx)
        split = run_split_backtest(assembler, ctx)
        self.backtests += int(split.backtests)
        if split.reject_reason:
            return ValidationOutcome(verdict="rejected", reject_reason=split.reject_reason,
                                     direction="long", metrics={}, series={})
        thresholds = self.thresholds()
        returns = bar_returns(split.is_points)
        report = check_gates(split.is_metrics, split.oos_metrics, thresholds=thresholds,
                             trials=trials, pool=self.pool, returns=returns)
        stress: dict = {}
        if not report.reason:
            multiplier = float(thresholds.get("costStressMult", 2.0))
            stress_metrics, extra = run_stress_is(assembler, ctx, cost_mult=multiplier)
            self.backtests += int(extra)
            stress = {"costMult": multiplier,
                      "totalReturn": stress_metrics.get("totalReturn")}
            check_stress(report, stress_metrics)
        metrics = self._metrics(candidate, ctx, split, report, thresholds, trials, stress)
        series = {"equityIs": _downsample(split.is_points),
                  "equityOos": _downsample(split.oos_points), "gates": report.gates}
        score = float(split.is_metrics.get("sharpe") or 0.0)
        if report.reason:
            return ValidationOutcome(verdict="rejected", reject_reason=report.reason,
                                     direction="long", metrics=metrics, series=series,
                                     score=score)
        return ValidationOutcome(verdict="kept", reject_reason="", direction="long",
                                 metrics=metrics, series=series, score=score,
                                 bar_returns=returns)

    def _metrics(self, candidate, ctx, split, report, thresholds, trials, stress) -> dict:
        """策略轴 ``metrics_json``（§5.1）；``costs`` 一律记 **bps 原值**（BL-1）。

        展示用的中文名（``templateName`` / 成员 ``label``）一律取 ``describe_strategy`` 的
        渲染——那是唯一真源。不带的话详情抽屉只能回显 ``momentum_breakout`` /
        ``expr:1a2b3c4d`` 这类裸标识符，正是本仓库反复踩的「中文化 helper 漏接展示点」。
        """
        from ..describe_strategy import param_diff, template_name

        initial, fee_bps, slip_bps, leverage = costs_of(ctx.settings)
        payload = {
            "kind": self.kind, "trials": int(trials),
            "requiredSharpe": round(float(report.required_sharpe), 6),
            "costs": {"initialCapital": initial, "takerFeeBps": fee_bps,
                      "slippageBps": slip_bps, "leverage": leverage},
            "clipMode": split.clip_mode, "warmupPad": int(split.pad),
            "is": split.is_metrics, "oos": split.oos_metrics,
            "maxEquityCorr": report.max_equity_corr,
            "thresholds": dict(thresholds),
        }
        if stress:
            payload["stress"] = stress
        if self.kind == "strategy_param":
            payload["templateKey"] = candidate.genome.template_key
            payload["templateName"] = template_name(candidate.genome.template_key)
            payload["paramDiff"] = param_diff(candidate.genome)
        if self.kind == "factor_combo":
            payload["members"] = _labeled_members(candidate.genome)
        if self.kind == "strategy_combo":
            payload["members"] = [{**spec, "label": template_name(spec["templateKey"])}
                                  for spec in candidate.genome.member_specs()]
            payload["policy"] = dict(candidate.genome.policy)
        return payload

    # ---------- 池 ----------
    def absorb(self, candidate, outcome, tally) -> None:
        if outcome.verdict != "kept":
            return
        tally.kept += 1
        self.pool.append(StrategyKeptEntry(
            candidate_id=0, kind=self.kind, genome_hash=candidate.genome_hash,
            genome=candidate.genome.payload(), bar_returns=list(outcome.bar_returns),
            score=float(outcome.score), family=candidate.mechanism,
            skeleton=candidate.skeleton))
        trim_pool(self.pool, self._max_kept())

    def _max_kept(self) -> int:
        return int(self.config.get("maxKeptPerAxis") or self.config.get("maxKept", 200))

    def rebuild(self, rows: list, ctx: LaneContext):
        """续跑 / 数据刷新后重建 S8 的去重基准（BL-5：一个实现两个调用点）。"""
        import json

        targets: list[StrategyKeptRow] = []
        for row in rows:
            if row.kind != self.kind:
                continue
            try:
                genome = json.loads(row.genome_json or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            targets.append(StrategyKeptRow(
                candidate_id=int(row.id), kind=row.kind, genome_hash=row.genome_hash,
                genome=genome if isinstance(genome, dict) else {},
                skeleton=row.skeleton or "", family=row.mechanism or ""))
        return rebuild_strategy_pool(
            targets, ctx, thresholds=self.thresholds(), run_split=run_split_backtest,
            build_assembler=lambda kind, payload: self.build_assembler(
                GENOME_LOADERS[kind](payload), ctx))

    def adopt(self, entries: list) -> None:
        self.pool = list(entries)

    def kept_genomes(self) -> list:
        """本轴 kept 池的基因（``strategy_combo`` 轴从这里取轮动成员，D-18）。"""
        out = []
        for entry in self.pool:
            try:
                out.append(GENOME_LOADERS[self.kind](entry.genome))
            except Exception:  # noqa: BLE001
                continue
        return out


def _labeled_members(genome) -> list[dict]:
    """因子组合成员 + 展示用中文名。

    ``label`` **只加在 metrics 上**，绝不混进 ``baked_members()`` 的产物——那份要原样交给
    ``validate_members`` 并最终落进入库参数，多一个键就违反 BL-7 的「逐键相等」。
    """
    from ..describe_strategy import member_label

    return [{**baked, "label": member_label(ref)}
            for baked, ref in zip(genome.baked_members(), genome.members)]


def assembler_for(kind: str, genome, settings):
    """genome → assembler 的**唯一**入口（挖掘验证、续跑重建、一键回测三处共用）。"""
    if kind == "strategy_param":
        return ParamAssembler(genome.template_key, genome.params)
    if kind == "factor_combo":
        return FactorComboAssembler(genome.baked_members(), genome.thresholds)
    return RotationAssembler(genome.member_specs(), genome.policy, settings)


def _downsample(points: list, cap: int = EQUITY_POINTS_CAP) -> list[dict]:
    """净值曲线下采样到 ≤``cap`` 点（详情抽屉够用，检查点与 WS 都不因此膨胀）。"""
    if not points:
        return []
    if len(points) <= cap:
        picked = points
    else:
        step = (len(points) - 1) / (cap - 1)
        picked = [points[min(len(points) - 1, round(i * step))] for i in range(cap)]
    return [{"ts": int(p.ts), "equity": round(float(p.equity), 4)} for p in picked]
