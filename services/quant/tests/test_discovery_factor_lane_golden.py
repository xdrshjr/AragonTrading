"""BL-8 等价门禁：因子轴在 Lane 重构前后**逐位不变**（discovery-strategy-axes §2.2）。

做法（spec §2.2 的四步，本文件是第 1 与第 3 步的载体）：
1. 在 Lane 拆分**之前**用当前实现跑 3 轮固定种子的合成窗口，把
   ``(genome_hash, verdict, reject_reason)`` 三元组序列与最终计数写进 ``GOLDEN``；
2. 做 Lane 拆分；
3. 重跑本用例——驱动换成 ``FactorLane``，``GOLDEN`` **一个字符都不许改**。

第 4 步（跨轴隔离，BL-14）另立在本文件末尾：golden 是 factor 单轴 3 轮，**照定义测不出**
「多轴同跑污染了因子轴的共享计数器」，故必须再加一条覆盖该维度的等价用例。

全程不连网、不落库：K 线是正弦 + 固定种子噪声，LLM 关闭（配额 0）。
"""
from __future__ import annotations

import math
import random

from app.config import Settings
from app.discovery.checkpoint import iteration_seed
from app.discovery.evolution import MomentumTracker, cold_start_budget, normalize_budget
from app.discovery.fsa import update_skeleton_stats
from app.discovery.iteration_stats import IterationTally
from app.discovery.lanes import FactorLane, LaneContext
from app.discovery.llm_agents import DiscoveryLlmAgents, HourlyCallBudget
from app.discovery.mechanisms import empty_coverage
from app.discovery.validation import split_index
from app.engine.backtester import candles_to_df

_TS = 1_700_000_000
SEED = 20260805
ROUNDS = 3
COUNT = 8

# ⚠️ 承重：本常量在 Lane 拆分**之前**由当前实现生成，拆分后必须逐条相等。
# 任何差异都视为回归，不接受「新实现更合理」的解释（spec §2.2 第 3 步）。
GOLDEN: dict = {
    "rounds": [
        [["ea665d911f9f1be7", "rejected", "homogeneous_simplify"],
         ["d16bb9500d901349", "rejected", "homogeneous_simplify"],
         ["c6b04a445b225bd7", "rejected", "constant_or_empty"],
         ["22966c5950cb3d41", "rejected", "F7"],
         ["eb518b5bf922d068", "rejected", "F2"],
         ["19c210578349f70d", "rejected", "constant_or_empty"],
         ["d5a6c7728d7ed790", "rejected", "F3"],
         ["4adc9c23abc32349", "kept", ""]],
        [["d4f353ceff1a00b1", "rejected", "homogeneous_simplify"],
         ["5bed0d043b52bfad", "rejected", "F2"],
         ["c4e9bfa95181d3a5", "rejected", "F10"],
         ["37757a0e611d8d38", "rejected", "F7"],
         ["9406668f83dbcc28", "kept", ""],
         ["2084386b3132fbe7", "rejected", "F10"],
         ["32911c9dabd61791", "rejected", "constant_or_empty"],
         ["2bec1c576cac0971", "rejected", "F2"]],
        [["b58a9a8c2a1acf85", "kept", ""],
         ["a5922e698f491864", "rejected", "F11"],
         ["5b02109caf7efb86", "rejected", "F11"],
         ["f5e2fa3dd69b865b", "rejected", "F9"],
         ["ad565c1fd6278bde", "rejected", "F9"],
         ["9b09097861985674", "rejected", "F10"],
         ["e4a048fb1871192a", "rejected", "F10"],
         ["9d848ca4e2cc44b9", "rejected", "F11"]],
    ],
    "counters": {"tested": 21, "kept": 3},
}


def _candles(n: int = 900) -> list[dict]:
    """确定性 K 线：正弦主信号 + 固定种子噪声（同种子同序列，绝不连网）。"""
    rng = random.Random(4242)
    out: list[dict] = []
    price = 100.0
    for i in range(n):
        sig = math.sin(i / 5.0) + 0.35 * math.sin(i / 23.0)
        close = price * (1.0 + 0.010 * sig + 0.0015 * (rng.random() - 0.5))
        out.append({"ts": _TS + i * 3600, "o": round(price, 6),
                    "h": round(max(price, close) * 1.0012, 6),
                    "l": round(min(price, close) * 0.9988, 6),
                    "c": round(close, 6),
                    "vol": round(1000.0 * (1.0 + 0.6 * sig) + 40.0 * rng.random(), 6)})
        price = close
    return out


def _config() -> dict:
    return {"horizon": 1, "isRatio": 0.7, "maxKept": 50, "candidatesPerIteration": COUNT,
            "thresholds": {}, "llm": {"enabled": False, "reviewSample": 0},
            "evolution": {"coldStartMin": 5, "adaptEvery": 10, "fsaEvery": 25,
                          "fsaBanTtlIters": 150, "variantCap": 6}}


def _lane_context(config: dict) -> LaneContext:
    df = candles_to_df(_candles())
    split = split_index(len(df), float(config["isRatio"]))
    return LaneContext(
        df_full=df, candles=[], htf_frames={}, bar="1H", split=split,
        split_ts=int(df["ts"].iloc[split]), config=config, settings=Settings(), memo={})


def _lane(config: dict) -> FactorLane:
    """LLM 全程关闭（配额 0 且无客户端）：本用例只测演化 + 验证的确定性部分。"""
    llm = DiscoveryLlmAgents(None, timeout_s=60.0, max_retries=0, budget=HourlyCallBudget(0))
    return FactorLane(config=config, momentum=MomentumTracker(), llm=llm,
                      coverage=empty_coverage())


async def _drive_factor_lane(rounds: int = ROUNDS) -> dict:
    """跑 ``rounds`` 轮因子轴闭环，返回 golden 形态的三元组序列与计数。"""
    config = _config()
    ctx = _lane_context(config)
    lane = _lane(config)
    out: list[list[list]] = []
    for index in range(1, rounds + 1):
        rng = random.Random(iteration_seed(SEED, index))
        budget = (cold_start_budget() if lane.pool_size() < 5
                  else normalize_budget(config["evolution"].get("budget")))
        tally = IterationTally()
        candidates, _outcome = await lane.produce(rng, budget=budget, count=COUNT, tally=tally)
        passed, rejected = lane.review(candidates)
        triples = []
        for candidate, reason in rejected:
            lane.mark_tested(candidate.genome_hash)     # 与 runtime 的 _rows_for 同序
            triples.append([candidate.genome_hash, "rejected", reason])
        for candidate in passed:
            outcome = lane.validate(candidate, ctx, trials=lane.tested + 1)
            lane.tested += 1
            update_skeleton_stats(lane.skeleton_stats, candidate.skeleton,
                                  kept=outcome.verdict == "kept")
            lane.mark_tested(candidate.genome_hash)
            lane.absorb(candidate, outcome, tally)
            triples.append([candidate.genome_hash, outcome.verdict,
                            outcome.reject_reason or ""])
        out.append(triples)
    return {"rounds": out, "counters": {"tested": lane.tested, "kept": lane.pool_size()}}


async def test_factor_lane_is_bit_identical_to_the_pre_refactor_baseline():
    """BL-8：三元组序列与计数必须与 ``GOLDEN`` 逐条相等。"""
    actual = await _drive_factor_lane()
    assert actual["rounds"] == GOLDEN["rounds"], (
        "因子轴行为发生漂移——Lane 重构必须是逐行平移，不得顺手「改好」任何一处")
    assert actual["counters"] == GOLDEN["counters"]


async def test_factor_axis_is_isolated_under_multi_axis():
    """BL-14（评审 P1-2）：多轴同跑不得改变因子轴的序列与 ``tested``。

    golden 用例是 factor 单轴，**照定义测不出**跨轴污染：现有实现里 ``tested`` /
    ``skeleton_stats`` / ``bans`` 都是 run 全局的，四轴同跑会把因子轴的 F3/F11 门槛抬高——
    一条已发布并已验收的轴，行为被本轮悄悄改掉。故本用例是逐轴计数的机器判据。
    """
    from app.discovery.lanes import StrategyLane

    config = _config()
    ctx = _lane_context(config)
    solo_triples, solo_tested = await _one_round(_lane(config), ctx)

    noisy = StrategyLane(kind="strategy_param", config=config, momentum=MomentumTracker(),
                         llm=None, catalog=None)
    noisy.tested = 5000                      # 另一条轴已测了五千次
    noisy.skeleton_stats["momentum_breakout"] = {"tested": 5000, "kept": 0}
    multi_triples, multi_tested = await _one_round(_lane(config), ctx)

    assert multi_triples == solo_triples == GOLDEN["rounds"][0], (
        "因子轴序列被另一条轴的计数器污染——多重检验分母必须逐轴")
    assert multi_tested == solo_tested


async def _one_round(lane, ctx) -> tuple[list, int]:
    """跑一轮因子轴闭环，返回 ``(三元组序列, 本轴已测数)``。"""
    rng = random.Random(iteration_seed(SEED, 1))
    tally = IterationTally()
    candidates, _outcome = await lane.produce(rng, budget=cold_start_budget(), count=COUNT,
                                              tally=tally)
    passed, rejected = lane.review(candidates)
    triples = []
    for candidate, reason in rejected:
        lane.mark_tested(candidate.genome_hash)
        triples.append([candidate.genome_hash, "rejected", reason])
    for candidate in passed:
        outcome = lane.validate(candidate, ctx, trials=lane.tested + 1)
        lane.tested += 1
        lane.mark_tested(candidate.genome_hash)
        lane.absorb(candidate, outcome, tally)
        triples.append([candidate.genome_hash, outcome.verdict, outcome.reject_reason or ""])
    return triples, lane.tested
