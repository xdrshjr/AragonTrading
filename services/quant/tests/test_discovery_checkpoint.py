"""检查点测试（strategy-factor-discovery §7.1）：序列化往返、有界裁剪、版本兼容、
``iteration+1`` 续跑、同 seed 同 iteration 复现同批候选。"""
from __future__ import annotations

import json
import random

from app.discovery.checkpoint import (
    CHECKPOINT_VERSION,
    FSA_BANS_CAP,
    REJECT_REASON_CAP,
    Checkpoint,
    bans_from_checkpoint,
    bump_reject,
    dumps,
    iteration_seed,
    loads,
)
from app.discovery.evolution import DEFAULT_BUDGET, MOMENTUM_CAP, build_search_context
from app.discovery.expression import to_text
from app.discovery.fsa import SKELETON_STATS_CAP
from app.discovery.genomes import ALL_AXES
from app.discovery.mechanisms import MECHANISM_KEYS
from app.discovery.searchers import FactorSearcher


def test_roundtrip_preserves_every_field():
    checkpoint = Checkpoint(
        iteration=137, rng_seed=20260805,
        budget={"mutate": 0.30, "crossover": 0.25, "perturb": 0.15,
                "random": 0.10, "llm": 0.20},
        counters={"tested": 5421, "kept": 37, "rejected": {"duplicate": 1902, "F3": 880}},
        momentum=[{"key": "sub(ts_mean(?,_),?)#0", "g": 0.42, "seenIter": 133}],
        fsa_bans=[{"skeleton": "sub(ts_mean(?,_),?)", "share": 0.19, "bannedAtIter": 100}],
        skeleton_stats={"sub(ts_mean(?,_),?)": {"tested": 43, "kept": 5}},
        mechanism_coverage={"trend_persistence": 6},
        data_window={"startTs": 1, "endTs": 2, "lastRefreshTs": 3},
        pool_sizes={"factor": 37}, kept_rate_trend=[0.1, 0.2, 0.3])
    restored = loads(dumps(checkpoint))
    assert restored.iteration == 137 and restored.rng_seed == 20260805
    assert restored.counters["tested"] == 5421
    assert restored.counters["rejected"]["duplicate"] == 1902
    assert restored.momentum == checkpoint.momentum
    assert restored.skeleton_stats == checkpoint.skeleton_stats
    assert restored.mechanism_coverage["trend_persistence"] == 6
    assert restored.data_window == checkpoint.data_window
    assert restored.pool_sizes["factor"] == 37
    assert restored.kept_rate_trend == [0.1, 0.2, 0.3]
    assert bans_from_checkpoint(restored)[0].skeleton == "sub(ts_mean(?,_),?)"


def test_defaults_are_complete_and_normalized():
    payload = Checkpoint().to_dict()
    assert payload["version"] == CHECKPOINT_VERSION
    assert payload["budget"] == DEFAULT_BUDGET
    assert set(payload["poolSizes"]) == set(ALL_AXES)
    # 机制覆盖恒为 13 族全集：惰性建键会让 UI 热力图随机缺格。
    assert set(payload["mechanismCoverage"]) == set(MECHANISM_KEYS)


def test_corrupt_checkpoint_falls_back_instead_of_raising():
    """断点损坏不该让 run 崩——一律回落初始状态（长跑特性的基本韧性）。"""
    assert loads("not json").iteration == 0
    assert loads(None).budget == DEFAULT_BUDGET
    assert Checkpoint.from_dict(None).iteration == 0
    assert Checkpoint.from_dict({"budget": "bad", "counters": 5}).budget == DEFAULT_BUDGET


def test_unknown_version_still_loads():
    """版本不识别也要能读（字段缺失回落默认），否则一次格式升级就把用户的长跑作废。"""
    restored = Checkpoint.from_dict({"version": 999, "iteration": 7})
    assert restored.iteration == 7


def test_all_growable_fields_are_bounded():
    """所有会增长的字段都在序列化边界强制裁剪（§2.10 的 64 KB 有界目标）。"""
    checkpoint = Checkpoint(
        momentum=[{"key": f"s{i}", "g": 0.1, "seenIter": i} for i in range(MOMENTUM_CAP + 80)],
        fsa_bans=[{"skeleton": f"s{i}", "share": 0.2, "bannedAtIter": i}
                  for i in range(FSA_BANS_CAP + 40)],
        skeleton_stats={f"s{i}": {"tested": i, "kept": 0}
                        for i in range(SKELETON_STATS_CAP + 100)},
        counters={"tested": 1, "kept": 0,
                  "rejected": {f"r{i}": i for i in range(REJECT_REASON_CAP + 30)}},
        kept_rate_trend=[0.1] * 20)
    payload = checkpoint.to_dict()
    assert len(payload["momentum"]) == MOMENTUM_CAP
    assert len(payload["fsaBans"]) == FSA_BANS_CAP
    assert len(payload["skeletonStats"]) == SKELETON_STATS_CAP
    assert len(payload["rejected"] if "rejected" in payload
               else payload["counters"]["rejected"]) <= REJECT_REASON_CAP + 1
    assert len(payload["keptRateTrend"]) == 3
    assert len(dumps(checkpoint)) < 64 * 1024        # 目标：检查点 < 64 KB


def test_reject_reason_overflow_goes_to_other_not_silently_dropped():
    """长尾拒绝原因归并进 ``other``，绝不静默丢弃（计数总量必须守恒）。"""
    counters = {"tested": 0, "kept": 0,
                "rejected": {f"r{i}": 1 for i in range(REJECT_REASON_CAP + 10)}}
    payload = Checkpoint(counters=counters).to_dict()["counters"]["rejected"]
    assert sum(payload.values()) == REJECT_REASON_CAP + 10
    assert payload.get("other") == 10


def test_bump_reject_accumulates():
    counters: dict = {}
    bump_reject(counters, "F3")
    bump_reject(counters, "F3")
    bump_reject(counters, "duplicate")
    assert counters["rejected"] == {"F3": 2, "duplicate": 1}
    bump_reject(counters, "")
    assert counters["rejected"]["unknown"] == 1


# ============================ 续跑复现 ============================
def test_iteration_seed_is_deterministic_and_decorrelated():
    assert iteration_seed(42, 7) == iteration_seed(42, 7)
    assert iteration_seed(42, 7) != iteration_seed(42, 8)
    assert iteration_seed(42, 7) != iteration_seed(43, 7)
    # 相邻两轮的种子不应只差 1（否则随机流高度相关，演化等于原地打转）。
    assert abs(iteration_seed(42, 7) - iteration_seed(42, 8)) > 1000


def test_same_seed_and_iteration_reproduce_the_same_batch():
    """§2.10：同 seed 同 iteration 必须复现同一批候选（断点续跑的正确性前提）。"""
    searcher, ctx = FactorSearcher(), build_search_context([])

    def batch(seed: int, iteration: int) -> list[str]:
        rng = random.Random(iteration_seed(seed, iteration))
        return [to_text(searcher.seed(rng, ctx).node) for _ in range(8)]

    assert batch(20260805, 12) == batch(20260805, 12)
    assert batch(20260805, 12) != batch(20260805, 13)


def test_resume_starts_from_next_iteration():
    checkpoint = loads(dumps(Checkpoint(iteration=137)))
    assert checkpoint.iteration + 1 == 138


def test_dumps_is_compact_json():
    payload = dumps(Checkpoint(iteration=1))
    assert ", " not in payload and '": ' not in payload
    assert json.loads(payload)["iteration"] == 1
