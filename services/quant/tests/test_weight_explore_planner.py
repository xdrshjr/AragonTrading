"""因子组合探索纯函数生成器测试（factor-combo-exploration §6.1）：sizes / topk_prefix / leave_one_out /
manual 的精确组合集与顺序、去重、成员数边界、退化池守门（|pool|==1 的 leave_one_out 不 ValueError）。

纯函数、零 IO，直接断言组合集与顺序（与前端 lib/weightExplore.ts::planCombos 同口径，C-7）。
"""
from __future__ import annotations

import pytest

from app.factors.weight_explore_planner import plan_combinations

_POOL = [
    {"factorKey": "a", "params": {}, "direction": "long"},
    {"factorKey": "b", "params": {}, "direction": "long"},
    {"factorKey": "c", "params": {}, "direction": "short"},
]


def _keys(plan) -> list[list[str]]:
    return [[m["factorKey"] for m in combo] for combo in plan.combos]


def test_sizes_enumerates_exact_c_n_k_in_order():
    plan = plan_combinations(_POOL, strategy="sizes", params={"sizes": [2, 3]})
    # C(3,2)=3 + C(3,3)=1；itertools 顺序：先全部 2 组合（按 pool 序），再 3 组合。
    assert _keys(plan) == [["a", "b"], ["a", "c"], ["b", "c"], ["a", "b", "c"]]
    assert plan.labels == ["a·b", "a·c", "b·c", "a·b·c"]


def test_topk_prefix_orders_by_rank_ic_desc_nested_prefix():
    plan = plan_combinations(_POOL, strategy="topk_prefix",
                             params={"sizes": [1, 2, 3], "rankIc": [0.1, 0.3, 0.2]})
    # rankIc 降序：b(0.3) > c(0.2) > a(0.1)；嵌套前缀 [b],[b,c],[b,c,a]。
    assert _keys(plan) == [["b"], ["b", "c"], ["b", "c", "a"]]


def test_topk_prefix_missing_rank_ic_falls_back_to_pool_order():
    plan = plan_combinations(_POOL, strategy="topk_prefix", params={"sizes": [2]})
    assert _keys(plan) == [["a", "b"]]   # 无 rankIc → 回落 pool 顺序，确定性、不报错


def test_leave_one_out_full_plus_each_dropped():
    plan = plan_combinations(_POOL, strategy="leave_one_out", params={})
    # 全池 1 个 + 逐一去 1 的 3 个 = 4 个。
    assert _keys(plan) == [["a", "b", "c"], ["b", "c"], ["a", "c"], ["a", "b"]]


def test_leave_one_out_single_pool_skips_empty_combo_no_error():
    single = [{"factorKey": "a", "params": {}, "direction": "long"}]
    plan = plan_combinations(single, strategy="leave_one_out", params={})
    assert _keys(plan) == [["a"]]        # |pool|==1：跳过空组合，仅留全池 1 个（P2-2，不 ValueError）


def test_manual_out_of_range_index_raises():
    with pytest.raises(ValueError):
        plan_combinations(_POOL, strategy="manual", params={"combos": [[0, 9]]})


def test_manual_explicit_empty_combo_raises():
    with pytest.raises(ValueError):
        plan_combinations(_POOL, strategy="manual", params={"combos": [[]]})


def test_manual_picks_indexed_subsets_and_dedups_within_entry():
    plan = plan_combinations(_POOL, strategy="manual", params={"combos": [[0, 1], [2, 2]]})
    assert _keys(plan) == [["a", "b"], ["c"]]   # [2,2] 组合内去重 → [c]


def test_dedups_identical_combos_across_sizes_by_member_identity():
    # sizes=[3] 与 leave_one_out 之外：这里用 sizes 含重复不会产生重复；改测跨策略同组合去重的语义
    # ——manual 指定两个等价组合（下标顺序不同但成员集合相同）只保留先出现者。
    plan = plan_combinations(_POOL, strategy="manual", params={"combos": [[0, 1], [1, 0]]})
    assert _keys(plan) == [["a", "b"]]          # {a,b} 只留一次（memberIdentity 排序集合去重）


def test_auto_strategy_skips_empty_but_keeps_nonempty():
    # sizes 含越界 k（>pool_size / <1）静默跳过，不影响合法 k。
    plan = plan_combinations(_POOL, strategy="sizes", params={"sizes": [0, 2, 99]})
    assert _keys(plan) == [["a", "b"], ["a", "c"], ["b", "c"]]
