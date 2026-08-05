"""五维演化测试（strategy-factor-discovery §7.1）：``adapt_budget`` 四条规则真值表、夹紧与
归一化、锦标赛选择分布、数据驱动分布、动量方向正确、状态有界（≤64）。"""
from __future__ import annotations

import random
from dataclasses import dataclass

import pytest

from app.discovery.evolution import (
    BUDGET_MAX,
    BUDGET_MIN,
    DEFAULT_BUDGET,
    MOMENTUM_CAP,
    SOURCE_KEYS,
    BudgetCounters,
    MomentumTracker,
    adapt_budget,
    allocate_operations,
    build_search_context,
    cold_start_budget,
    normalize_budget,
    tournament_select,
)
from app.discovery.expression import WINDOW_LADDER, parse_expression


@dataclass
class _Entry:
    score: float


def _sums_to_one(budget: dict) -> bool:
    return abs(sum(budget.values()) - 1.0) < 1e-6


# ============================ 预算基础 ============================
def test_default_budget_matches_the_report():
    assert DEFAULT_BUDGET == {"mutate": 0.25, "crossover": 0.25, "perturb": 0.15,
                              "random": 0.15, "llm": 0.20}
    assert _sums_to_one(DEFAULT_BUDGET)


def test_cold_start_puts_everything_on_random_and_llm():
    """冷启动无父本可变异 / 交叉，三个近邻操作份额必须为 0（否则整轮空转）。"""
    budget = cold_start_budget()
    assert budget["random"] == budget["llm"] == 0.5
    assert budget["mutate"] == budget["crossover"] == budget["perturb"] == 0.0


def test_normalize_handles_missing_negative_and_all_zero():
    assert _sums_to_one(normalize_budget({"mutate": 1.0}))
    assert normalize_budget({key: -1.0 for key in SOURCE_KEYS}) == DEFAULT_BUDGET
    assert normalize_budget(None)["llm"] == pytest.approx(0.20)


# ============================ adapt_budget 四条规则真值表 ============================
def test_rule1_near_neighbourhood_pays_off_shifts_to_exploitation():
    counters = BudgetCounters(near_kept_rate=0.20, global_kept_rate=0.05)
    out = adapt_budget(DEFAULT_BUDGET, counters)
    assert out["mutate"] > DEFAULT_BUDGET["mutate"]
    assert out["perturb"] > DEFAULT_BUDGET["perturb"]
    assert _sums_to_one(out)


def test_rule2_high_correlation_rejections_shift_to_exploration():
    counters = BudgetCounters(dedupe_reject_rate=0.45)
    out = adapt_budget(DEFAULT_BUDGET, counters)
    assert out["random"] > DEFAULT_BUDGET["random"]
    assert out["llm"] > DEFAULT_BUDGET["llm"]


def test_rule3_uncovered_family_boosts_llm_only_after_probe_window():
    early = adapt_budget(DEFAULT_BUDGET, BudgetCounters(uncovered_families=4, iteration=10))
    assert early["llm"] == pytest.approx(DEFAULT_BUDGET["llm"])
    late = adapt_budget(DEFAULT_BUDGET, BudgetCounters(uncovered_families=4, iteration=60))
    assert late["llm"] > DEFAULT_BUDGET["llm"]


def test_rule4_declining_yield_with_coverage_boosts_crossover():
    declining = BudgetCounters(covered_families=5, kept_rate_trend=(0.09, 0.06, 0.03))
    assert adapt_budget(DEFAULT_BUDGET, declining)["crossover"] > DEFAULT_BUDGET["crossover"]
    flat = BudgetCounters(covered_families=5, kept_rate_trend=(0.05, 0.05, 0.05))
    assert adapt_budget(DEFAULT_BUDGET, flat)["crossover"] == pytest.approx(
        DEFAULT_BUDGET["crossover"])
    # 样本不足（<3 窗）一律判否，绝不外推趋势。
    short = BudgetCounters(covered_families=5, kept_rate_trend=(0.09, 0.03))
    assert adapt_budget(DEFAULT_BUDGET, short)["crossover"] == pytest.approx(
        DEFAULT_BUDGET["crossover"])


def test_no_trigger_keeps_budget_unchanged():
    assert adapt_budget(DEFAULT_BUDGET, BudgetCounters()) == pytest.approx(DEFAULT_BUDGET)


def test_clamp_then_normalize_never_starves_a_source():
    """任一分量恒在 ``[0.05, 0.40]`` 夹紧后归一化——绝不让某个来源被彻底饿死。"""
    skewed = {"mutate": 0.99, "crossover": 0.0, "perturb": 0.0, "random": 0.0, "llm": 0.0}
    out = adapt_budget(skewed, BudgetCounters(near_kept_rate=1.0, global_kept_rate=0.01))
    assert _sums_to_one(out)
    lower = BUDGET_MIN / (BUDGET_MAX + 4 * BUDGET_MIN)
    assert all(value >= lower - 1e-6 for value in out.values())   # 6 位小数舍入容差


# ============================ 名额分配 ============================
def test_allocate_operations_respects_budget_and_count():
    rng = random.Random(3)
    plan = allocate_operations(DEFAULT_BUDGET, 40, rng)
    assert len(plan) == 40
    assert plan.count("mutate") == 10 and plan.count("llm") == 8
    assert set(plan) <= set(SOURCE_KEYS)
    assert allocate_operations(DEFAULT_BUDGET, 0, rng) == []


def test_allocate_operations_gives_small_shares_at_least_one_slot():
    """最大余额法：15% 的份额在 20 个名额里必须真的拿到 3 个，而不是听天由命。"""
    plan = allocate_operations(DEFAULT_BUDGET, 20, random.Random(5))
    assert plan.count("perturb") == 3 and plan.count("random") == 3


def test_allocate_is_reproducible_for_the_same_seed():
    assert (allocate_operations(DEFAULT_BUDGET, 40, random.Random(9))
            == allocate_operations(DEFAULT_BUDGET, 40, random.Random(9)))


# ============================ 锦标赛选择 ============================
def test_tournament_prefers_high_score_but_is_not_argmax():
    """锦标赛既要偏向高分，又必须给次优父本非零概率（防过早收敛到单一结构家族）。"""
    pool = [_Entry(score=float(i)) for i in range(10)]
    rng = random.Random(0)
    picks = [tournament_select(rng, pool, size=3).score for _ in range(400)]
    assert sum(picks) / len(picks) > 6.0          # 明显偏向高分
    assert len(set(picks)) > 3                     # 但绝非恒取全局最优
    assert tournament_select(rng, []) is None
    assert tournament_select(rng, [_Entry(1.0)]).score == 1.0


# ============================ 数据驱动特征分布 ============================
def test_cold_start_distribution_is_uniform_with_no_priors():
    """kept 为空时字段权重全 1——绝不给 ``overnight`` 之类字段任何先验加权。

    报告里 overnight 是 A 股最强信号源，但在 7×24 永续上跨根跳空极弱；照抄先验就是把
    A 股结论硬套过来（§一 边界与诚实声明）。
    """
    ctx = build_search_context([])
    assert set(ctx.field_weights.values()) == {1.0}
    assert set(ctx.window_weights.values()) == {1.0}


def test_kept_fields_get_up_weighted():
    kept = [parse_expression("sub(ts_mean(overnight,20),delta(amplitude,5))"),
            parse_expression("ts_zscore(ts_sum(overnight,20),90)")]
    ctx = build_search_context(kept)
    assert ctx.field_weights["overnight"] == pytest.approx(3.0)   # 1 + 2×100%
    assert ctx.field_weights["amplitude"] == pytest.approx(2.0)   # 1 + 2×50%
    assert ctx.field_weights["volume"] == pytest.approx(1.0)      # 未出现


def test_window_distribution_centres_on_kept_median():
    kept = [parse_expression("ts_mean(ret,90)"), parse_expression("ts_mean(ret,90)"),
            parse_expression("ts_mean(ret,120)")]
    ctx = build_search_context(kept)
    peak = max(ctx.window_weights, key=lambda w: ctx.window_weights[w])
    assert peak == 90
    assert ctx.window_weights[WINDOW_LADDER[0]] < ctx.window_weights[90]
    assert min(ctx.window_weights.values()) >= 0.2      # 远端不归零，保留跳出局部最优的可能


# ============================ 动量追踪 ============================
def test_momentum_direction_follows_the_observed_gradient():
    tracker = MomentumTracker()
    key = "sub(ts_mean(?,_),?)"
    for _ in range(5):
        tracker.observe(key, 0, delta_score=0.4, delta_index=1, iteration=10)
    assert tracker.step_for(key, 0) > 0
    for _ in range(10):
        tracker.observe(key, 1, delta_score=-0.4, delta_index=1, iteration=10)
    assert tracker.step_for(key, 1) < 0
    assert tracker.step_for("never-seen", 0) == 1     # 无记录 ⇒ 最保守的「走一格」


def test_momentum_step_is_capped():
    tracker = MomentumTracker()
    tracker.observe("hot", 0, delta_score=100.0, delta_index=1, iteration=1)
    tracker.observe("cold", 0, delta_score=0.0001, delta_index=1, iteration=1)
    assert abs(tracker.step_for("hot", 0)) <= 4


def test_momentum_state_is_bounded_and_roundtrips():
    tracker = MomentumTracker()
    for i in range(MOMENTUM_CAP + 30):
        tracker.observe(f"s{i}", 0, delta_score=0.1, delta_index=1, iteration=i)
    assert len(tracker) == MOMENTUM_CAP
    restored = MomentumTracker.from_list(tracker.to_list())
    assert len(restored) == MOMENTUM_CAP
    assert restored.to_list() == tracker.to_list()
    assert len(MomentumTracker.from_list(None)) == 0
    assert len(MomentumTracker.from_list([{"bad": 1}])) == 0


def test_momentum_ignores_zero_delta_index():
    tracker = MomentumTracker()
    tracker.observe("s", 0, delta_score=1.0, delta_index=0, iteration=1)
    assert len(tracker) == 0
