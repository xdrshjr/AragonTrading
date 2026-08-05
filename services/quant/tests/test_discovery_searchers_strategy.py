"""三个策略搜索器的同构用例（discovery-strategy-axes §7.1）。

统一契约：**产物合法或 ``None``**——梯子越界不可能、跨模板交叉必须降级并留痕、权重严格归一。
搜索器是第一道防线；审查层的规则是第二道，不是第一道。
"""
from __future__ import annotations

import random

from app.discovery.catalogs import SearchInputs, minable_templates
from app.discovery.genomes import ComboMemberRef, ParamGenome
from app.discovery.searchers import ComboSearcher, ParamSearcher, RotationSearcher
from app.discovery.searchers.rotation_searcher import MINABLE_POLICY_KEYS

SAMPLES = 300


def _param_inputs() -> SearchInputs:
    return SearchInputs(templates=minable_templates("1H", max_htf_frames=3).templates)


def _members(count: int = 5) -> tuple:
    return tuple(ComboMemberRef(factor_key="expr",
                                params={"expr": f"sub(close,ts_mean(close,{20 + i * 5}))"},
                                direction="long", name=f"expr:{i:08x}")
                 for i in range(count))


def _assert_on_ladder(genome: ParamGenome, inputs: SearchInputs) -> None:
    spec = next(s for s in inputs.templates if s.key == genome.template_key)
    assert spec.minable
    for param in spec.params:
        assert genome.params[param.key] in param.ladder, f"{param.key} 越出梯子"
    assert set(genome.params) == {param.key for param in spec.params}


# ============================ ParamSearcher ============================
def test_param_seed_and_mutate_stay_on_the_ladder():
    searcher, inputs, rng = ParamSearcher(), _param_inputs(), random.Random(1)
    parent = searcher.seed(rng, inputs)
    _assert_on_ladder(parent, inputs)
    for _ in range(SAMPLES):
        child = searcher.mutate(rng, parent, inputs)
        assert child is not None and child.template_key == parent.template_key
        _assert_on_ladder(child, inputs)


def test_param_mutation_actually_changes_something():
    """重掷一格且**不等于原值**：等于原值的「变异」只是白付一次回测。"""
    searcher, inputs, rng = ParamSearcher(), _param_inputs(), random.Random(2)
    parent = searcher.seed(rng, inputs)
    changed = [searcher.mutate(rng, parent, inputs) for _ in range(50)]
    assert any(child.params != parent.params for child in changed)


def test_cross_template_crossover_degrades_and_leaves_a_trace():
    searcher, inputs, rng = ParamSearcher(), _param_inputs(), random.Random(3)
    minable = [spec for spec in inputs.templates if spec.minable]
    left = ParamGenome(minable[0].key,
                       {p.key: p.ladder[0] for p in minable[0].params})
    right = ParamGenome(minable[1].key,
                        {p.key: p.ladder[0] for p in minable[1].params})
    child = searcher.crossover(rng, left, right, inputs)
    assert child is not None and child.template_key == left.template_key
    assert inputs.degraded["crossover_degraded"] == 1


def test_param_from_llm_refuses_unknown_templates_and_out_of_range_values():
    searcher, inputs = ParamSearcher(), _param_inputs()
    assert searcher.from_llm({"templateKey": "nope", "params": {}}, inputs) is None
    assert searcher.from_llm({"templateKey": "momentum_breakout"}, inputs) is None
    assert searcher.from_llm({"templateKey": "momentum_breakout",
                              "params": {"breakout_window": 1e9}}, inputs) is None
    good = searcher.from_llm({"templateKey": "momentum_breakout",
                              "params": {"breakout_window": 34}}, inputs)
    assert good is not None and good.params["breakout_window"] == 34


def test_param_perturb_keeps_the_structure():
    searcher, inputs, rng = ParamSearcher(), _param_inputs(), random.Random(4)
    parent = searcher.seed(rng, inputs)
    child = searcher.perturb(rng, parent, inputs, None)
    assert child.template_key == parent.template_key
    _assert_on_ladder(child, inputs)


# ============================ ComboSearcher ============================
def test_combo_products_are_legal_and_normalised():
    searcher = ComboSearcher()
    inputs = SearchInputs(members=_members(), combo_size=(2, 4))
    rng = random.Random(5)
    parent = searcher.seed(rng, inputs)
    for genome in [parent] + [searcher.mutate(rng, parent, inputs) for _ in range(SAMPLES)]:
        assert genome is not None
        assert 2 <= len(genome.members) <= 4
        assert len(genome.weights) == len(genome.members)
        assert abs(sum(genome.weights) - 1.0) < 1e-9
        assert all(member.direction in ("long", "short") for member in genome.members)


def test_combo_crossover_merges_members_and_renormalises():
    searcher = ComboSearcher()
    inputs = SearchInputs(members=_members(), combo_size=(2, 3))
    rng = random.Random(6)
    left, right = searcher.seed(rng, inputs), searcher.seed(rng, inputs)
    child = searcher.crossover(rng, left, right, inputs)
    assert child is not None and abs(sum(child.weights) - 1.0) < 1e-9
    names = [member.name for member in child.members]
    assert len(set(names)) == len(names)


def test_combo_from_llm_rejects_members_outside_the_catalog():
    searcher = ComboSearcher()
    inputs = SearchInputs(members=_members(3), combo_size=(2, 3))
    assert searcher.from_llm({"members": [{"name": "expr:ffffffff"}]}, inputs) is None
    payload = {"members": [{"name": "expr:00000000"}, {"name": "expr:00000001"}],
               "weights": [3.0, 1.0], "thresholds": {}}
    genome = searcher.from_llm(payload, inputs)
    assert genome is not None and abs(sum(genome.weights) - 1.0) < 1e-9


def test_combo_seed_returns_none_when_the_catalog_is_too_small():
    searcher = ComboSearcher()
    assert searcher.seed(random.Random(7),
                         SearchInputs(members=_members(1), combo_size=(2, 3))) is None


# ============================ RotationSearcher ============================
def test_rotation_members_come_only_from_the_upstream_pool():
    searcher = RotationSearcher()
    pool = tuple(ParamGenome("momentum_breakout", {"breakout_window": w})
                 for w in (10, 20, 30, 40))
    inputs = SearchInputs(rotation_pool=pool, rotation_size=(2, 4))
    rng = random.Random(8)
    for _ in range(SAMPLES):
        genome = searcher.seed(rng, inputs)
        assert genome is not None and 2 <= len(genome.members) <= 4
        assert all(member in pool for member in genome.members)
        assert set(genome.policy) == set(MINABLE_POLICY_KEYS)


def test_rotation_requires_distinct_member_parameters():
    """同模板可重复出现，但**参数必须不同**（否则组合退化成一个成员）。"""
    searcher = RotationSearcher()
    same = ParamGenome("momentum_breakout", {"breakout_window": 20})
    inputs = SearchInputs(rotation_pool=(same, same, same), rotation_size=(2, 4))
    assert searcher.seed(random.Random(9), inputs) is None


def test_rotation_does_not_accept_llm_generation():
    """D-14：成员必须来自本 run kept 池，模型无法凭空引用。"""
    assert RotationSearcher().from_llm({"members": []}, SearchInputs()) is None
