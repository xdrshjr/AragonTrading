"""三条策略轴的入库路径（discovery-strategy-axes §2.11）。

- **BL-7**：``params_json`` 与**被回测的那份参数逐键相等**，不塞任何溯源字段。
  「挖的时候是 A、入库后跑的是 A+x」是本仓库反复吃亏的静默背离。
- **P1-9**：``factor_combo`` 的幂等回放必须补齐 ``companionStrategyId``，否则第二次入库后
  成果库的「去策略面板」跳转就断了。
- 安全不变式：入库只写 ``factors`` / ``factor_sets`` / ``strategies`` 三张表。
"""
from __future__ import annotations

import json

from sqlalchemy import delete, select

from app.db.models import (
    DiscoveryCandidate,
    DiscoveryIteration,
    DiscoveryRun,
    Factor,
    FactorSet,
    Strategy,
)
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.discovery import promote
from app.discovery import repositories as repo
from app.discovery.genomes import (
    ComboGenome,
    ComboMemberRef,
    ParamGenome,
    RotationGenome,
    genome_hash,
)

_TS = 1_700_000_000
_PARAMS = {"surge_pct": 0.02, "breakout_window": 34, "vol_mult": 1.8, "trail_pct": 0.02,
           "stop_loss_pct": 0.03, "max_hold_bars": 48, "strength": 0.9}
_EXPRS = ("sub(ts_mean(close,20),ts_mean(close,60))", "div(volume,ts_mean(volume,20))")


async def _fresh():
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        await session.execute(delete(DiscoveryCandidate))
        await session.execute(delete(DiscoveryIteration))
        await session.execute(delete(DiscoveryRun))
        await session.execute(delete(Factor).where(Factor.source == "mined"))
        await session.execute(delete(Strategy).where(Strategy.builtin == False))  # noqa: E712
        await session.execute(delete(FactorSet))
        await session.commit()
    return sm


def _combo_genome() -> ComboGenome:
    members = tuple(ComboMemberRef(factor_key="expr", params={"expr": expr},
                                   direction="long", name=f"expr:{i:08x}")
                    for i, expr in enumerate(_EXPRS))
    return ComboGenome(members=members, weights=(0.6, 0.4),
                       thresholds={"enterLong": 0.25, "enterShort": 0.25, "exitBand": 0.1})


def _rotation_genome() -> RotationGenome:
    return RotationGenome(
        members=(ParamGenome("momentum_breakout", {"breakout_window": 20}),
                 ParamGenome("ema_cross_scalper", {"fast": 9, "slow": 21})),
        policy={"rebalanceBars": 24, "lookbackBars": 168, "minDwellBars": 24,
                "switchMarginPct": 0.1})


async def _seed(sm, *, kind: str, genome: dict, skeleton: str = "sk") -> int:
    async with sm() as session:
        run = await repo.create_run(session, values=repo.RunValues(
            label="x", inst_id="BTC-USDT-SWAP", bar="1H", symbols=["BTC-USDT-SWAP"],
            config={}, checkpoint={}, started_ts=_TS))
        await repo.commit_iteration(
            session, run.id,
            candidates=[repo.CandidateRow(
                kind=kind, genome_hash=genome_hash(genome), genome=genome, expr_text="",
                describe_text="挖到的东西", skeleton=skeleton, mechanism="breakout",
                source="seed", verdict="kept", metrics={}, series={}, trials_at_accept=3,
                created_ts=_TS)],
            iteration=repo.IterationRow(idx=1, started_ts=_TS, finished_ts=_TS, budget={},
                                        stats={}, llm={}),
            checkpoint_json="{}", counters={"tested": 1, "kept": 1},
            candidate_row_cap=100, iteration_row_cap=100)
        row = (await repo.list_candidates(session, run.id, verdict=None))[0][0]
    return int(row.id)


# ============================ strategy_param ============================
async def test_param_candidate_becomes_a_strategy_row_with_identical_params():
    sm = await _fresh()
    try:
        genome = ParamGenome("momentum_breakout", _PARAMS).payload()
        candidate_id = await _seed(sm, kind="strategy_param", genome=genome)
        async with sm() as session:
            payload = await promote.promote_candidate(session, candidate_id)
            row = await session.get(Strategy, payload["promoted"]["entityId"])
        assert payload["promoted"]["kind"] == "strategy"
        assert row.template_key == "momentum_breakout"
        # BL-7：逐键相等，且**没有任何多余键**（溯源只在候选侧）。
        assert json.loads(row.params_json) == _PARAMS
        assert row.name.startswith("挖掘策略 ") and len(row.name) <= 128
    finally:
        await dispose_engine()


async def test_promoting_twice_returns_the_same_strategy_id():
    sm = await _fresh()
    try:
        genome = ParamGenome("momentum_breakout", _PARAMS).payload()
        candidate_id = await _seed(sm, kind="strategy_param", genome=genome)
        async with sm() as session:
            first = await promote.promote_candidate(session, candidate_id)
            second = await promote.promote_candidate(session, candidate_id)
            rows = (await session.execute(select(Strategy).where(
                Strategy.template_key == "momentum_breakout"))).scalars().all()
        assert first["promoted"]["entityId"] == second["promoted"]["entityId"]
        assert second["promoted"]["reused"] is True
        assert len([r for r in rows if not r.builtin]) == 1
    finally:
        await dispose_engine()


# ============================ strategy_combo ============================
async def test_rotation_candidate_becomes_a_regime_rotation_strategy():
    sm = await _fresh()
    try:
        genome = _rotation_genome().payload()
        candidate_id = await _seed(sm, kind="strategy_combo", genome=genome)
        async with sm() as session:
            payload = await promote.promote_candidate(session, candidate_id)
            row = await session.get(Strategy, payload["promoted"]["entityId"])
        params = json.loads(row.params_json)
        assert row.template_key == "regime_rotation"
        assert [m["templateKey"] for m in params["members"]] == [
            "momentum_breakout", "ema_cross_scalper"]
        assert params["rebalanceBars"] == 24 and "members" in params
        assert row.name.startswith("挖掘轮动 ")
    finally:
        await dispose_engine()


async def test_rotation_with_a_single_member_is_rejected_at_the_boundary():
    """入库期与挖掘期用同一把尺子：``validate_combo_params`` 要求 ≥2 成员。"""
    from fastapi import HTTPException

    sm = await _fresh()
    try:
        genome = RotationGenome(members=(ParamGenome("momentum_breakout", {}),),
                                policy={}).payload()
        candidate_id = await _seed(sm, kind="strategy_combo", genome=genome)
        async with sm() as session:
            try:
                await promote.promote_candidate(session, candidate_id)
                raise AssertionError("单成员轮动竟然入库成功了")
            except HTTPException as exc:
                assert exc.detail["code"] == "COMBO_MEMBERS"
    finally:
        await dispose_engine()


# ============================ factor_combo ============================
async def test_combo_candidate_creates_a_factor_set_and_its_companion_strategy():
    sm = await _fresh()
    try:
        candidate_id = await _seed(sm, kind="factor_combo", genome=_combo_genome().payload())
        async with sm() as session:
            payload = await promote.promote_candidate(session, candidate_id)
            entity_id = payload["promoted"]["entityId"]
            row = await session.get(FactorSet, entity_id)
            companion = await session.get(Strategy, payload["promoted"]["companionStrategyId"])
        assert payload["promoted"]["kind"] == "factor_set"
        members = json.loads(row.members_json)
        assert len(members) == 2
        assert abs(sum(float(m["weight"]) for m in members) - 1.0) < 1e-9
        assert companion is not None and companion.template_key == "factor_score"
        assert json.loads(companion.params_json)["factorSetId"] == entity_id
    finally:
        await dispose_engine()


async def test_replaying_a_combo_promote_still_carries_the_companion_id():
    """P1-9：现有幂等分支直接返回不带该字段的 payload ⇒ 第二次入库后跳转会断。"""
    sm = await _fresh()
    try:
        candidate_id = await _seed(sm, kind="factor_combo", genome=_combo_genome().payload())
        async with sm() as session:
            first = await promote.promote_candidate(session, candidate_id)
            second = await promote.promote_candidate(session, candidate_id)
        assert second["promoted"]["reused"] is True
        assert second["promoted"]["companionStrategyId"]
        assert (second["promoted"]["companionStrategyId"]
                == first["promoted"]["companionStrategyId"])
    finally:
        await dispose_engine()


async def test_combo_promote_goes_through_create_set(monkeypatch):
    """必须走 ``factors.service.create_set``（单事务 + 伴生策略 + 三道校验），绝不自建行。"""
    from app.factors import service as factors_service

    sm = await _fresh()
    calls: list = []
    original = factors_service.create_set

    async def _spy(session, **kwargs):
        calls.append(kwargs["name"])
        return await original(session, **kwargs)

    monkeypatch.setattr(factors_service, "create_set", _spy)
    try:
        candidate_id = await _seed(sm, kind="factor_combo", genome=_combo_genome().payload())
        async with sm() as session:
            await promote.promote_candidate(session, candidate_id)
        assert len(calls) == 1 and calls[0].startswith("挖掘组合 ")
    finally:
        await dispose_engine()


# ============================ 安全不变式 ============================
async def test_promoting_strategy_axes_never_writes_run_history():
    """入库只写三张实体表；``strategy_runs`` 一行都不许多。

    ⚠️ 全量跑时整个 tests/ 共用一个临时 DB，别的用例可能已经留下运行历史行——判据必须是
    「**增量**为 0」而不是「表为空」，否则这条安全断言会被无关用例打红。
    """
    sm = await _fresh()
    try:
        from app.db.models import StrategyRun

        candidate_id = await _seed(sm, kind="strategy_param",
                                   genome=ParamGenome("momentum_breakout", _PARAMS).payload())
        async with sm() as session:
            before = len((await session.execute(select(StrategyRun))).scalars().all())
            await promote.promote_candidate(session, candidate_id)
            after = len((await session.execute(select(StrategyRun))).scalars().all())
        assert after == before
    finally:
        await dispose_engine()
