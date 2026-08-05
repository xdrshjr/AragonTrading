"""搜索器测试（strategy-factor-discovery §7.1）：五种操作的产物合法、同种子可复现、
LLM 产出非法即拒。

**范围（D-6）**：v1 只有 ``factor`` 轴，故本文件只测 ``FactorSearcher``；其余三轴落地时
在此追加同构用例。
"""
from __future__ import annotations

import random

from app.discovery.evolution import MomentumTracker, SearchContext, build_search_context
from app.discovery.expression import (
    MAX_DEPTH,
    MAX_NODES,
    MIN_NODES,
    WINDOW_LADDER,
    depth,
    is_valid,
    node_count,
    parse_expression,
    to_text,
    windows_of,
)
from app.discovery.genomes import (
    ALL_AXES,
    IMPLEMENTED_AXES,
    ExprGenome,
    Searcher,
    make_factor_candidate,
)
from app.discovery.review import ReviewContext, review_structure
from app.discovery.searchers import FactorSearcher

_PARENTS = ("sub(ts_mean(overnight,20),delta(amplitude,5))",
            "div(sub(close,ts_mean(close,20)),ts_std(close,20))",
            "ts_corr(ret,ts_zscore(volume,20),30)",
            "mul(sign(ts_mean(ret,60)),ts_rank(ret,20))")


def _genomes():
    return [ExprGenome(parse_expression(text)) for text in _PARENTS]


def _assert_legal(genome: ExprGenome) -> None:
    assert is_valid(genome.node), to_text(genome.node)
    assert MIN_NODES <= node_count(genome.node) <= MAX_NODES
    assert depth(genome.node) <= MAX_DEPTH
    assert all(w in WINDOW_LADDER for w in windows_of(genome.node))


# ============================ 范围守门（D-7） ============================
def test_all_four_axes_are_implemented():
    """D-7：四条轴全部落地 ⇒ ``IMPLEMENTED_AXES == ALL_AXES``。

    这条守的仍是同一件事：**能选但不产候选**算缺陷，不算完成。轴一旦进
    ``IMPLEMENTED_AXES``，就必须有搜索器、有验证、有入库路径。
    """
    from app.discovery.searchers import SEARCHERS

    assert IMPLEMENTED_AXES == ALL_AXES
    assert isinstance(FactorSearcher(), Searcher)
    assert FactorSearcher.kind == "factor"
    for axis in ALL_AXES:
        if axis == "factor":
            continue
        searcher = SEARCHERS[axis]()
        assert isinstance(searcher, Searcher) and searcher.kind == axis


# ============================ seed ============================
def test_seed_always_returns_legal_genomes():
    searcher, ctx, rng = FactorSearcher(), build_search_context([]), random.Random(7)
    produced = [searcher.seed(rng, ctx) for _ in range(200)]
    assert all(g is not None for g in produced)
    for genome in produced:
        _assert_legal(genome)


def test_seed_output_mostly_survives_the_review_rules():
    """生成器不是「先随便长再让审查筛」：绝大多数产物应当直接过规则链。"""
    searcher, ctx, rng = FactorSearcher(), build_search_context([]), random.Random(11)
    review = ReviewContext()
    genomes = [searcher.seed(rng, ctx) for _ in range(200)]
    passed = sum(1 for g in genomes
                 if review_structure(make_factor_candidate(g.node, "random"), review) == "")
    assert passed / len(genomes) > 0.6


def test_seed_is_reproducible_for_the_same_seed():
    searcher, ctx = FactorSearcher(), build_search_context([])
    first = [to_text(searcher.seed(random.Random(4), ctx).node) for _ in range(5)]
    second = [to_text(searcher.seed(random.Random(4), ctx).node) for _ in range(5)]
    assert first == second


def test_seed_honours_the_data_driven_field_distribution():
    """字段权重高的字段应显著更常出现（报告的「第一次自适应」真的接线了）。"""
    searcher, rng = FactorSearcher(), random.Random(2)
    ctx = SearchContext(field_weights={"overnight": 200.0}, window_weights={})
    hits = 0
    for _ in range(120):
        genome = searcher.seed(rng, ctx)
        if genome is not None and "overnight" in to_text(genome.node):
            hits += 1
    assert hits > 60


def test_mechanism_hint_biases_towards_that_family():
    searcher, rng = FactorSearcher(), random.Random(13)
    ctx = build_search_context([], mechanism_hint="shadow_pressure")
    texts = [to_text(searcher.seed(rng, ctx).node) for _ in range(150)]
    shadow = sum(1 for t in texts if "shadow" in t)
    assert shadow > 20


# ============================ mutate / crossover / perturb ============================
def test_mutate_produces_legal_and_different_genomes():
    searcher, ctx, rng = FactorSearcher(), build_search_context([]), random.Random(5)
    parents = _genomes()
    produced = [searcher.mutate(rng, parents[i % len(parents)], ctx) for i in range(120)]
    got = [g for g in produced if g is not None]
    assert len(got) > 100
    for genome in got:
        _assert_legal(genome)
    assert any(to_text(g.node) not in _PARENTS for g in got)


def test_crossover_mixes_two_parents_legally():
    searcher, ctx, rng = FactorSearcher(), build_search_context([]), random.Random(6)
    parents = _genomes()
    got = [g for g in (searcher.crossover(rng, parents[i % 4], parents[(i + 1) % 4], ctx)
                       for i in range(120)) if g is not None]
    assert len(got) > 100
    for genome in got:
        _assert_legal(genome)


def test_perturb_keeps_structure_and_only_moves_windows():
    """参数扰动是四类操作中最保守的一种：骨架必须逐字不变，只有窗口在梯子上移动。"""
    from app.discovery.expression import skeleton

    searcher, ctx, rng = FactorSearcher(), build_search_context([]), random.Random(8)
    parent = ExprGenome(parse_expression(_PARENTS[0]))
    moved = 0
    for _ in range(60):
        genome = searcher.perturb(rng, parent, ctx, MomentumTracker())
        if genome is None:
            continue
        moved += 1
        _assert_legal(genome)
        assert skeleton(genome.node) == skeleton(parent.node)
        assert windows_of(genome.node) != windows_of(parent.node)
    assert moved > 30


def test_perturb_follows_momentum_direction():
    """动量为正 ⇒ 窗口往梯子的大方向走；这是「扰动有方向」而非乱走的判据。"""
    searcher, ctx = FactorSearcher(), build_search_context([])
    parent = ExprGenome(parse_expression("ts_mean(ts_zscore(ret,20),20)"))
    tracker = MomentumTracker()
    from app.discovery.expression import skeleton

    key = skeleton(parent.node)
    for slot in (0, 1):
        for _ in range(8):
            tracker.observe(key, slot, delta_score=0.5, delta_index=1, iteration=1)
    genome = searcher.perturb(random.Random(1), parent, ctx, tracker)
    assert genome is not None
    assert max(windows_of(genome.node)) > 20


def test_perturb_returns_none_without_window_slots():
    searcher, ctx = FactorSearcher(), build_search_context([])
    parent = ExprGenome(parse_expression("sub(close,typical)"))
    assert searcher.perturb(random.Random(1), parent, ctx, MomentumTracker()) is None


# ============================ from_llm ============================
def test_from_llm_accepts_valid_and_rejects_everything_else():
    searcher, ctx = FactorSearcher(), build_search_context([])
    good = searcher.from_llm({"expr": _PARENTS[0]}, ctx)
    assert good is not None and to_text(good.node) == _PARENTS[0]
    for payload in ({}, {"expr": ""}, {"expr": "nope(ret,20)"}, {"expr": "ts_mean(ret,21)"},
                    {"expr": "add(close,volume)"}, {"expr": "ts_mean(ret,20)"}, None):
        assert searcher.from_llm(payload, ctx) is None, payload
