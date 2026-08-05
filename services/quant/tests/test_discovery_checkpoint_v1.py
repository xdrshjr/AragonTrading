"""老检查点兼容与逐轴计数回落（discovery-strategy-axes §2.10 · BL-14）。

**承重回落**：缺 ``testedByAxis`` / ``skeletonStatsByAxis`` / ``fsaBansByAxis`` 时，把既有的
全局值整体归给 ``factor`` 轴。归零会让一个跑了两周的因子任务在升级后把 ``trials`` 清零，
F3/F11 的多重检验惩罚整体塌回基准值，第二天就会收进一批本该被惩罚掉的噪声因子。
"""
from __future__ import annotations

import json

from app.discovery.checkpoint import Checkpoint, dumps, loads
from app.discovery.genomes import ALL_AXES
from app.discovery.validation import required_ir

_V1_PAYLOAD = {
    "version": 1, "iteration": 812, "rngSeed": 20260805,
    "budget": {"mutate": 0.25, "crossover": 0.25, "perturb": 0.15, "random": 0.15,
               "llm": 0.20},
    "counters": {"tested": 18342, "kept": 63, "rejected": {"F10": 900}},
    "momentum": [], "fsaBans": [{"skeleton": "sub(ts_mean(?,_),?)", "share": 0.31,
                                 "bannedAtIter": 700}],
    "skeletonStats": [["sub(ts_mean(?,_),?)", {"tested": 900, "kept": 40}]],
    "mechanismCoverage": {}, "dataWindow": {"startTs": 1, "endTs": 2, "lastRefreshTs": 3},
    "poolSizes": {"factor": 63}, "keptRateTrend": [0.01, 0.02, 0.015],
}


def test_v1_checkpoint_loads_without_any_new_field():
    checkpoint = Checkpoint.from_dict(_V1_PAYLOAD)
    assert checkpoint.iteration == 812 and checkpoint.rng_seed == 20260805
    assert checkpoint.pool_sizes["factor"] == 63
    assert checkpoint.axis_shares == {axis: 0 for axis in ALL_AXES}
    assert checkpoint.member_catalog_hash == "" and checkpoint.template_signature == ""


def test_missing_tested_by_axis_falls_back_to_the_factor_axis():
    """归零会让多重检验惩罚一夜塌回基准值——这条断言就是那次事故的护栏。"""
    checkpoint = Checkpoint.from_dict(_V1_PAYLOAD)
    assert checkpoint.tested_by_axis["factor"] == 18342
    assert all(checkpoint.tested_by_axis[axis] == 0 for axis in ALL_AXES
               if axis != "factor")
    thresholds = {"minAbsIr": 0.5, "trialPenaltyK": 0.15}
    before = required_ir(18342, thresholds)
    after = required_ir(checkpoint.tested_by_axis["factor"], thresholds)
    assert after == before, "续跑后因子轴门槛必须与升级前逐位相等"
    assert after > required_ir(0, thresholds), "归零会让门槛整体塌回基准值"


def test_missing_skeleton_stats_and_bans_also_fall_back_to_factor():
    checkpoint = Checkpoint.from_dict(_V1_PAYLOAD)
    assert checkpoint.skeleton_stats_by_axis["factor"] == {
        "sub(ts_mean(?,_),?)": {"tested": 900, "kept": 40}}
    assert checkpoint.skeleton_stats_by_axis["strategy_param"] == {}
    assert len(checkpoint.fsa_bans_by_axis["factor"]) == 1
    assert checkpoint.fsa_bans_by_axis["factor_combo"] == []


def test_explicit_per_axis_fields_win_over_the_fallback():
    payload = {**_V1_PAYLOAD,
               "testedByAxis": {"factor": 5, "strategy_param": 7},
               "skeletonStatsByAxis": {"strategy_param": [["momentum_breakout",
                                                           {"tested": 9, "kept": 1}]]},
               "fsaBansByAxis": {"strategy_param": [{"skeleton": "momentum_breakout",
                                                     "share": 0.4, "bannedAtIter": 3}]}}
    checkpoint = Checkpoint.from_dict(payload)
    assert checkpoint.tested_by_axis == {"factor": 5, "factor_combo": 0,
                                         "strategy_param": 7, "strategy_combo": 0}
    assert checkpoint.skeleton_stats_by_axis["strategy_param"]["momentum_breakout"][
        "tested"] == 9
    assert checkpoint.fsa_bans_by_axis["strategy_param"][0]["skeleton"] == "momentum_breakout"


def test_round_trip_keeps_every_new_field_bounded():
    checkpoint = Checkpoint.from_dict(_V1_PAYLOAD)
    checkpoint.axis_shares = {"factor": 20, "strategy_param": 20}
    checkpoint.member_catalog_hash = "abc123"
    checkpoint.template_signature = "def456"
    checkpoint.tested_by_axis = {"factor": 1, "strategy_param": 2}
    payload = json.loads(dumps(checkpoint))
    assert set(payload["axisShares"]) == set(ALL_AXES)
    assert payload["memberCatalogHash"] == "abc123"
    assert payload["templateSignature"] == "def456"
    assert payload["testedByAxis"]["strategy_param"] == 2
    assert set(payload["skeletonStatsByAxis"]) == set(ALL_AXES)
    assert len(dumps(checkpoint)) < 64 * 1024, "检查点必须保持有界（目标 < 64 KB）"


def test_corrupt_payload_still_falls_back_instead_of_raising():
    assert loads("not json").iteration == 0
    assert Checkpoint.from_dict({"counters": 5}).tested_by_axis["factor"] == 0
    assert Checkpoint.from_dict({"testedByAxis": "bad"}).tested_by_axis["factor"] == 0
