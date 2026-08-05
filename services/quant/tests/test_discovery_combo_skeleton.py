"""BL-10（评审 P0-1）：组合两轴的骨架必须落在**语义结构**上。

按 ``factorKey`` 拼接会退化成一个常量——所有挖掘因子的 ``factor_key`` 都是字面量 ``"expr"``。
后果两条且都不报错：``variant_cap``(6) 从第 7 个入库候选起全量拒死该轴；FSA 再把这唯一骨架
冻结 150 轮。表现是「这条轴挖了两天一个都没产出」，日志里一条错误也没有。
"""
from __future__ import annotations

from app.discovery.catalogs import SearchInputs, minable_templates
from app.discovery.fsa import scan_skeletons
from app.discovery.genomes import (
    ComboGenome,
    ComboMemberRef,
    combo_skeleton,
    make_combo_candidate,
    member_skeleton_key,
    rotation_skeleton,
)
from app.discovery.lanes import StrategyLane

_EXPR_A = "sub(ts_mean(close,20),ts_mean(close,60))"
_EXPR_B = "ts_zscore(volume,30)"
_EXPR_C = "div(sub(high,low),close)"


def _member(expr: str, name: str) -> ComboMemberRef:
    return ComboMemberRef(factor_key="expr", params={"expr": expr}, direction="long",
                          name=name)


def _combo(*exprs: str) -> ComboGenome:
    members = tuple(_member(expr, f"expr:{i:08x}") for i, expr in enumerate(exprs))
    weight = round(1.0 / len(members), 6)
    return ComboGenome(members=members, weights=tuple([weight] * len(members)),
                       thresholds={"enterLong": 0.3, "enterShort": 0.3, "exitBand": 0.1})


def test_two_combos_with_different_members_get_different_skeletons():
    """P0-1 的机器判据：成员完全不同的两个组合，骨架**必须**不同。"""
    left = combo_skeleton(_combo(_EXPR_A, _EXPR_B).members)
    right = combo_skeleton(_combo(_EXPR_C, _EXPR_B).members)
    assert left != right, "骨架退化成常量——variant_cap 会从第 7 个候选起全量拒死该轴"


def test_member_skeleton_key_uses_expression_structure_not_the_literal_key():
    """``expr`` 成员取表达式骨架；库内因子仍取 ``factorKey``。"""
    expr_key = member_skeleton_key(_member(_EXPR_A, "expr:0000000a"))
    assert expr_key.startswith("expr#") and expr_key != "expr"
    library = ComboMemberRef(factor_key="mom_roc", params={}, direction="long",
                             name="mom_roc")
    assert member_skeleton_key(library) == "mom_roc"


def test_same_members_in_any_order_share_one_skeleton():
    """骨架按成员语义键排序后拼接：成员顺序不同不该被当成两种结构。"""
    assert combo_skeleton(_combo(_EXPR_A, _EXPR_B).members) == combo_skeleton(
        _combo(_EXPR_B, _EXPR_A).members)


def test_candidate_carries_the_semantic_skeleton():
    candidate = make_combo_candidate(_combo(_EXPR_A, _EXPR_B), "seed")
    assert candidate.skeleton == combo_skeleton(candidate.genome.members)
    assert candidate.mechanism == "factor_combo"


def test_rotation_skeleton_is_the_sorted_template_set():
    from app.discovery.genomes import ParamGenome, RotationGenome

    left = RotationGenome(members=(ParamGenome("momentum_breakout", {"breakout_window": 20}),
                                   ParamGenome("ema_cross_scalper", {"fast": 9})), policy={})
    right = RotationGenome(members=(ParamGenome("ema_cross_scalper", {"fast": 12}),
                                    ParamGenome("momentum_breakout", {"breakout_window": 30})),
                           policy={})
    assert rotation_skeleton(left.members) == rotation_skeleton(right.members)


# ============================ FSA 最小池护栏（BL-10 后半） ============================
def test_fsa_freezes_everything_when_the_pool_is_tiny():
    """池里只有 3 条时每条都占 33% > 15% ⇒ 会被**全部**冻结。这就是护栏存在的理由。"""
    bans = scan_skeletons(["a", "b", "c"], iteration=25, threshold=0.15)
    assert len(bans) == 3


def test_strategy_param_threshold_adapts_to_the_template_vocabulary():
    """8 元词表上 15% 意味着「占了 1/6 就冻结 150 轮」——几乎必然命中最好的那两三个模板。"""
    catalog = minable_templates("1H", max_htf_frames=3)
    lane = StrategyLane(kind="strategy_param", config={}, momentum=None, llm=None,
                        catalog=SearchInputs(templates=catalog.templates))
    minable = sum(1 for spec in catalog.templates if spec.minable)
    assert minable >= 2
    assert lane.fsa_threshold(0.15) == max(0.15, 2.0 / minable)


def test_factor_combo_threshold_is_untouched():
    lane = StrategyLane(kind="factor_combo", config={}, momentum=None, llm=None,
                        catalog=SearchInputs())
    assert lane.fsa_threshold(0.15) == 0.15
