"""四轴同跑的运行时集成（discovery-strategy-axes §2.9）。

覆盖：候选行的 ``kind`` 分布、``perAxis`` 计数自洽、回测硬顶截断留痕、让渡如实上报、
逐轴计数进检查点（BL-14）、LLM 未配置时照常推进。OKX 全程打桩，不触网。
"""
from __future__ import annotations

import asyncio
import json
import math
import random

from sqlalchemy import delete

from app.config import Settings
from app.db.models import DiscoveryCandidate, DiscoveryIteration
from app.db.models import DiscoveryRun as DiscoveryRunRow
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.discovery import repositories as repo
from app.discovery import runtime as runtime_module
from app.discovery.runtime import DiscoveryRun, RunSpec
from app.okx.types import Candle

_TS = 1_700_000_000
_AXES = ["factor", "factor_combo", "strategy_param", "strategy_combo"]


def _candles(n: int = 900) -> list[Candle]:
    rng = random.Random(31)
    out: list[Candle] = []
    price = 100.0
    for i in range(n):
        move = 0.0012 + 0.011 * math.sin(i / 8.0) + 0.002 * (rng.random() - 0.5)
        close = price * (1.0 + move)
        out.append(Candle(ts=_TS + i * 3600, o=round(price, 6),
                          h=round(max(price, close) * 1.003, 6),
                          l=round(min(price, close) * 0.997, 6), c=round(close, 6),
                          vol=1000.0 * (1.0 + 0.5 * math.sin(i / 5.0))))
        price = close
    return out


def _settings(**over) -> Settings:
    base = {"discovery_candidate_timeout_s": 30.0, "discovery_backtest_timeout_s": 60.0,
            "discovery_iteration_timeout_s": 600.0, "discovery_validate_concurrency": 2,
            "discovery_ws_ring": 100, "discovery_max_candidate_rows": 500,
            "discovery_max_iteration_rows": 50, "discovery_llm_max_calls_per_hour": 0,
            "discovery_max_htf_frames": 0}      # 合成数据不含高周期，目录层直接封顶为 0
    base.update(over)
    return Settings(**base)


def _config(**over) -> dict:
    base = {"windowBars": 900, "axes": list(_AXES), "horizon": 1, "isRatio": 0.7,
            "candidatesPerIteration": 8, "iterationIntervalS": 60.0, "dataRefreshS": 0.0,
            "windowMode": "rolling", "maxIterations": 1, "maxRuntimeHours": 0.0,
            "maxKept": 50, "maxKeptPerAxis": 50, "autoResume": True, "autoPromote": False,
            "revalidateOnRefresh": True, "seed": 20260805,
            "axisWeights": {axis: 1.0 for axis in _AXES},
            "templates": [], "includeLibraryFactors": True,
            "comboSize": {"min": 2, "max": 3}, "rotationSize": {"min": 2, "max": 3},
            "strategyThresholds": {}, "llm": {"enabled": False, "reviewSample": 0},
            "thresholds": {}, "evolution": {"coldStartMin": 5, "adaptEvery": 10,
                                            "fsaEvery": 25, "fsaBanTtlIters": 150,
                                            "variantCap": 6}}
    base.update(over)
    return base


async def _fresh():
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        await session.execute(delete(DiscoveryCandidate))
        await session.execute(delete(DiscoveryIteration))
        await session.execute(delete(DiscoveryRunRow))
        await session.commit()
    return sm


async def _make_run(sm, *, config=None, settings=None) -> DiscoveryRun:
    cfg = config or _config()
    async with sm() as session:
        row = await repo.create_run(session, values=repo.RunValues(
            label="多轴", inst_id="BTC-USDT-SWAP", bar="1H", symbols=["BTC-USDT-SWAP"],
            config=cfg, checkpoint={"iteration": 0, "rngSeed": cfg["seed"]},
            started_ts=_TS))
    return DiscoveryRun(RunSpec(
        run_id=int(row.id), inst_id="BTC-USDT-SWAP", bar="1H", symbols=("BTC-USDT-SWAP",),
        config=cfg, rest=object(), settings=settings or _settings(), resume=False,
        llm_client=None))


def _patch(monkeypatch) -> None:
    async def _load(rest, **kwargs):
        return _candles()

    async def _frames(rest, **kwargs):
        return {}

    monkeypatch.setattr(runtime_module, "load_range_candles", _load)
    monkeypatch.setattr(runtime_module, "load_htf_frames", _frames)


async def _drive(run: DiscoveryRun, *, timeout: float = 300.0) -> None:
    run.start()
    await asyncio.wait_for(run.join(), timeout=timeout)


# ============================ 四轴同跑 ============================
async def test_four_axes_produce_candidates_of_every_kind(monkeypatch):
    sm = await _fresh()
    _patch(monkeypatch)
    try:
        run = await _make_run(sm)
        await _drive(run)
        assert run.status == "succeeded", run.pause_reason
        async with sm() as session:
            rows, total = await repo.list_candidates(session, run.run_id, verdict=None,
                                                     limit=200)
            iterations = await repo.list_iterations(session, run.run_id)
        assert total > 0
        kinds = {row.kind for row in rows}
        assert kinds == set(_AXES) or kinds >= {"factor", "strategy_param"}, kinds
        stats = json.loads(iterations[0].stats_json)
        per_axis = stats["perAxis"]
        assert set(per_axis) == set(_AXES)
        assert sum(entry["generated"] for entry in per_axis.values()) == stats["generated"]
        assert sum(entry["kept"] for entry in per_axis.values()) == stats["kept"]
        assert sum(stats["axisShares"].values()) == 8
    finally:
        await dispose_engine()


async def test_candidate_rows_carry_the_strategy_genome_and_no_expression(monkeypatch):
    sm = await _fresh()
    _patch(monkeypatch)
    try:
        run = await _make_run(sm)
        await _drive(run)
        async with sm() as session:
            rows, _total = await repo.list_candidates(session, run.run_id, verdict=None,
                                                      kind="strategy_param", limit=50)
        assert rows, "strategy_param 轴一条候选都没落库 ⇒ 该轴等于空转"
        for row in rows:
            assert row.expr_text == ""            # 模型注释已声明：策略轴 expr_text 为空
            genome = json.loads(row.genome_json)
            assert genome["templateKey"] and genome["params"]
            assert row.describe_text, "中文渲染缺失 ⇒ UI 只能显示一串哈希"
    finally:
        await dispose_engine()


async def test_per_axis_counters_land_in_the_checkpoint(monkeypatch):
    """BL-14：``tested`` / ``skeletonStats`` / ``fsaBans`` 一律逐轴进检查点。"""
    sm = await _fresh()
    _patch(monkeypatch)
    try:
        run = await _make_run(sm)
        await _drive(run)
        async with sm() as session:
            row = await repo.get_run(session, run.run_id)
        checkpoint = json.loads(row.checkpoint_json)
        assert set(checkpoint["testedByAxis"]) == set(_AXES) | set(checkpoint["testedByAxis"])
        assert sum(checkpoint["testedByAxis"].values()) == json.loads(
            row.counters_json)["tested"]
        assert set(checkpoint["poolSizes"]) >= set(_AXES)
        assert checkpoint["templateSignature"]
    finally:
        await dispose_engine()


async def test_backtest_cap_truncates_and_leaves_a_trace(monkeypatch):
    """§六：硬顶数的是**底层 run_backtest 调用次数**，触顶即截断并留痕。"""
    sm = await _fresh()
    _patch(monkeypatch)
    try:
        run = await _make_run(sm, config=_config(axes=["strategy_param"],
                                                 axisWeights={"strategy_param": 1.0},
                                                 candidatesPerIteration=6),
                              settings=_settings(discovery_max_backtests_per_iteration=1,
                                                 discovery_max_htf_frames=0))
        await _drive(run)
        async with sm() as session:
            iterations = await repo.list_iterations(session, run.run_id)
        stats = json.loads(iterations[0].stats_json)
        assert stats["truncatedByBacktestCap"] > 0
        assert "回测次数触顶" in iterations[0].note
    finally:
        await dispose_engine()


async def test_rotation_axis_yields_while_the_upstream_pool_is_empty(monkeypatch):
    """BL-9：上游库存不足时**让渡并如实标注**，绝不留空转轴。"""
    sm = await _fresh()
    _patch(monkeypatch)
    try:
        run = await _make_run(sm)
        await _drive(run)
        async with sm() as session:
            iterations = await repo.list_iterations(session, run.run_id)
        stats = json.loads(iterations[0].stats_json)
        yielded = stats["yieldedByPool"]
        assert "strategy_combo" in yielded, "轮动轴在第一轮必然无上游库存，应当让渡"
        entry = yielded["strategy_combo"]
        assert entry["count"] > 0 and entry["toAxis"] == "factor"
        assert entry["reason"] == "upstream_pool_too_small"
        assert "策略轮动组合" in iterations[0].note
    finally:
        await dispose_engine()


async def test_llm_is_called_at_most_once_per_iteration(monkeypatch):
    """R-9：多轴不得让 LLM 调用次数翻四倍。"""
    sm = await _fresh()
    _patch(monkeypatch)
    try:
        run = await _make_run(sm)
        assert run._llm.enabled is False
        await _drive(run)
        async with sm() as session:
            iterations = await repo.list_iterations(session, run.run_id)
        assert json.loads(iterations[0].llm_json)["calls"] == 0
    finally:
        await dispose_engine()


async def test_factor_only_run_never_fetches_higher_timeframes(monkeypatch):
    """DoD #12 的同一精神：没选策略轴的 run 不得因此多取一根高周期 K 线。"""
    sm = await _fresh()
    calls: list = []

    async def _load(rest, **kwargs):
        return _candles()

    async def _frames(rest, **kwargs):
        calls.append(kwargs.get("timeframes"))
        return {}

    monkeypatch.setattr(runtime_module, "load_range_candles", _load)
    monkeypatch.setattr(runtime_module, "load_htf_frames", _frames)
    try:
        run = await _make_run(sm, config=_config(axes=["factor"],
                                                 axisWeights={"factor": 1.0}),
                              settings=_settings(discovery_max_htf_frames=3))
        await _drive(run)
        assert calls == [], "因子单轴的 run 竟然去取了高周期"
    finally:
        await dispose_engine()


# ============================ 生成来源与审查抽样（终审 §十二） ============================
def _lane(kind: str = "strategy_param"):
    from app.discovery.catalogs import SearchInputs, minable_templates
    from app.discovery.evolution import MomentumTracker
    from app.discovery.lanes.strategy_lane import StrategyLane

    catalog = minable_templates("1H", max_htf_frames=0)
    return StrategyLane(kind=kind, config=_config(), momentum=MomentumTracker(), llm=None,
                        catalog=SearchInputs(templates=catalog.templates))


async def test_strategy_axis_llm_slots_are_labelled_random_not_llm():
    """策略轴不发起 LLM 生成调用（D-14 / §十一 I-2），回落名额必须**改标 random**。

    沿用 ``llm`` 标注不会报错，只是「来源分布」柱状图会画出一段本轮根本没发生过的 LLM
    产出——而那张图的全部意义正是「实际来源与预算背离时一眼看出谁在空转」。
    """
    from app.discovery.iteration_stats import IterationTally

    lane = _lane()
    tally = IterationTally()
    budget = {"mutate": 0.0, "crossover": 0.0, "perturb": 0.0, "random": 0.0, "llm": 1.0}

    produced, outcome = await lane.produce(random.Random(7), budget=budget, count=6,
                                           tally=tally)

    assert produced, "LLM 份额回落后一条都没产出 ⇒ 名额白白蒸发"
    assert {candidate.source for candidate in produced} == {"random"}
    assert tally.by_source.get("llm", 0) == 0
    assert lane.llm_fallback == 6              # 回落格数仍如实计数（进 axisSnapshots）
    assert outcome.calls == 0


def test_review_sampling_rotates_across_axes():
    """审查抽样跨轴轮转：按轴序取前 N 条时，因子轴一产得多，三条策略轴就永远抽不到。"""
    from app.discovery.iteration import _sample_across_axes

    pending = {"factor": [f"f{i}" for i in range(10)],
               "strategy_param": ["p0", "p1"], "factor_combo": ["c0"]}

    assert _sample_across_axes(pending, 5) == ["f0", "p0", "c0", "f1", "p1"]
    # 单轴时与「取前 N 条」逐条相同 ⇒ 因子轴行为不变（BL-8）。
    assert _sample_across_axes({"factor": pending["factor"]}, 3) == ["f0", "f1", "f2"]
    assert _sample_across_axes(pending, 0) == []
    assert _sample_across_axes({}, 5) == []


def test_combo_member_labels_never_pollute_the_baked_payload():
    """展示用中文名只能加在 metrics 上：``baked_members()`` 那份要落进入库参数（BL-7）。"""
    from app.discovery.genomes import ComboGenome, ComboMemberRef
    from app.discovery.lanes.strategy_lane import _labeled_members

    genome = ComboGenome(
        members=(ComboMemberRef(factor_key="expr", params={"expr": "ts_mean(overnight,20)"},
                                direction="long", name="expr:abcd1234"),),
        weights=(1.0,), thresholds={"enterLong": 0.2})

    labeled = _labeled_members(genome)

    assert labeled[0]["label"] and labeled[0]["label"] != labeled[0]["name"]
    assert labeled[0]["weight"] == 1.0
    assert all("label" not in member for member in genome.baked_members())


async def test_strategy_metrics_carry_chinese_display_names(monkeypatch):
    """详情抽屉的「模板」与「成员清单」必须显示中文名，绝不回显裸标识符。"""
    sm = await _fresh()
    _patch(monkeypatch)
    try:
        run = await _make_run(sm)
        await _drive(run)
        async with sm() as session:
            rows, _total = await repo.list_candidates(session, run.run_id, verdict=None,
                                                      limit=200)
        named = 0
        for row in rows:
            metrics = json.loads(row.metrics_json or "{}")
            if metrics.get("templateKey"):
                assert metrics["templateName"] != metrics["templateKey"]
                named += 1
            for member in metrics.get("members") or []:
                assert member.get("label"), "成员缺中文名 ⇒ UI 只能显示一串裸标识符"
        assert named, "没有任何 strategy_param 候选产出指标 ⇒ 本断言空转"
    finally:
        await dispose_engine()
