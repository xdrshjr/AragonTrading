"""审查层测试（strategy-factor-discovery §7.1）：九条规则逐条命中 + 合法表达式全部放行。"""
from __future__ import annotations

import numpy as np
import pytest

from app.discovery.expression import parse_expression
from app.discovery.genomes import make_factor_candidate
from app.discovery.mechanisms import MECHANISM_FAMILIES
from app.discovery.review import (
    REVIEW_REASON_CN,
    ReviewContext,
    filter_rules,
    reason_cn,
    review_structure,
    review_values,
)


def _candidate(text: str, source: str = "random"):
    return make_factor_candidate(parse_expression(text), source)


@pytest.mark.parametrize("text", [
    "ts_rank(ts_rank(ret,20),60)",
    "sign(sign(ret))",
    "abs(abs(sub(close,open)))",
    "ts_mean(ts_mean(ret,20),60)",
    "neg(neg(ts_mean(ret,20)))",
])
def test_degenerate_outer(text):
    assert review_structure(_candidate(text), ReviewContext()) == "degenerate_outer"


def test_double_ema_and_second_difference_stay_allowed():
    """双重平滑 / 二阶差分是真实技术，不在退化集里——规则不得无谓砍掉搜索空间。"""
    ctx = ReviewContext()
    assert review_structure(_candidate("ema(ema(close,20),60)"), ctx) == ""
    assert review_structure(_candidate("delta(delta(close,20),5)"), ctx) == ""


@pytest.mark.parametrize("text", [
    "sub(ts_mean(ret,20),ts_mean(ret,20))",
    "div(ts_mean(ret,20),ts_mean(ret,20))",
    "add(ts_mean(ret,20),0)",
    "mul(ts_mean(ret,20),1)",
    "mul(ts_mean(ret,20),0)",
])
def test_homogeneous_simplify(text):
    assert review_structure(_candidate(text), ReviewContext()) == "homogeneous_simplify"


def test_dimension_mismatch_is_caught():
    """跨量纲表达式（``add(价格, 成交量)``）必须被挡住。

    ``op_node`` 只查元数与窗口、**不查量纲**（量纲判定是审查层的职责），故此处直接构树。
    """
    from dataclasses import replace

    from app.discovery.expression import Node, field_node
    from app.discovery.genomes import ExprGenome

    bad = Node(op="add", args=(field_node("close"), field_node("volume")))
    candidate = replace(_candidate("sub(ts_mean(ret,20),ts_mean(ret,60))"),
                        genome=ExprGenome(bad))
    assert review_structure(candidate, ReviewContext()) == "dimension_mismatch"


def test_complexity_out_of_range():
    """节点数低于最小复杂度门槛也是「越界」（报告的最小复杂度要求）。"""
    assert review_structure(_candidate("ts_mean(ret,20)"),
                            ReviewContext()) == "complexity_out_of_range"


def test_duplicate_against_tested_hashes():
    candidate = _candidate("sub(ts_mean(overnight,20),delta(amplitude,5))")
    ctx = ReviewContext(tested_hashes={candidate.genome_hash})
    assert review_structure(candidate, ctx) == "duplicate"


def test_fsa_banned_and_variant_cap_and_blacklist():
    candidate = _candidate("sub(ts_mean(overnight,20),delta(amplitude,5))")
    key = candidate.skeleton
    assert review_structure(candidate, ReviewContext(banned_skeletons={key})) == "fsa_banned"
    assert review_structure(candidate, ReviewContext(
        kept_skeleton_counts={key: 6}, variant_cap=6)) == "variant_cap"
    assert review_structure(candidate, ReviewContext(
        skeleton_stats={key: {"tested": 20, "kept": 0}})) == "skeleton_blacklisted"
    # 已测 20 次但入库过 1 条 ⇒ 不是失败模式，照常放行。
    assert review_structure(candidate, ReviewContext(
        skeleton_stats={key: {"tested": 40, "kept": 1}})) == ""


def test_constant_or_empty_needs_values():
    ctx = ReviewContext(min_coverage=0.6)
    assert review_values(np.full(100, 1.0), ctx) == "constant_or_empty"      # 方差为 0
    sparse = np.full(100, np.nan)
    sparse[:30] = np.arange(30, dtype=float)
    assert review_values(sparse, ctx) == "constant_or_empty"                 # 覆盖率 0.3 < 0.6
    good = np.arange(100, dtype=float)
    assert review_values(good, ctx) == ""


def test_all_mechanism_prototypes_pass_the_rule_chain():
    """26 个机制原型必须**全部放行**——审查规则不得误伤设计里给出的参考结构。"""
    ctx = ReviewContext()
    for family in MECHANISM_FAMILIES:
        for text in family.prototypes:
            assert review_structure(_candidate(text), ctx) == "", f"{family.key}: {text}"


def test_filter_rules_dedupes_within_one_batch():
    """同批里的重复表达式必须被挡住，否则 commit 时唯一索引会让整轮回滚。"""
    text = "sub(ts_mean(overnight,20),delta(amplitude,5))"
    passed, rejected = filter_rules([_candidate(text), _candidate(text)], ReviewContext())
    assert len(passed) == 1
    assert [reason for _c, reason in rejected] == ["duplicate"]


def test_filter_rules_partitions_and_keeps_order():
    candidates = [_candidate("sub(ts_mean(overnight,20),delta(amplitude,5))"),
                  _candidate("sign(sign(ret))"),
                  _candidate("div(ts_std(ret,20),ts_std(ret,90))")]
    passed, rejected = filter_rules(candidates, ReviewContext())
    assert [c.expr_text for c in passed] == [candidates[0].expr_text, candidates[2].expr_text]
    assert rejected == [(candidates[1], "degenerate_outer")]


def test_every_rule_has_a_chinese_reason():
    for code in ("degenerate_outer", "homogeneous_simplify", "dimension_mismatch",
                 "complexity_out_of_range", "duplicate", "fsa_banned", "variant_cap",
                 "skeleton_blacklisted", "constant_or_empty"):
        assert code in REVIEW_REASON_CN and reason_cn(code) != code
    assert reason_cn("no_such_code") == "no_such_code"
