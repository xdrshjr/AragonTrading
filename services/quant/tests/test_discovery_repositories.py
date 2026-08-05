"""挖掘三表数据访问测试（strategy-factor-discovery §7.1）：单事务 ``commit_iteration`` 的
原子性、唯一约束去重、剪枝计数如实、启动对账。"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import delete

from app.db.models import DiscoveryCandidate, DiscoveryIteration, DiscoveryRun
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.discovery import repositories as repo

_TS = 1_700_000_000


async def _fresh():
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        await session.execute(delete(DiscoveryCandidate))
        await session.execute(delete(DiscoveryIteration))
        await session.execute(delete(DiscoveryRun))
        await session.commit()
    return sm


def _values(**over) -> repo.RunValues:
    base = {"label": "BTC 1H 挖掘", "inst_id": "BTC-USDT-SWAP", "bar": "1H",
            "symbols": ["BTC-USDT-SWAP"], "config": {"axes": ["factor"]},
            "checkpoint": {"iteration": 0}, "started_ts": _TS}
    base.update(over)
    return repo.RunValues(**base)


def _candidate(hash_: str, verdict: str = "kept", **over) -> repo.CandidateRow:
    base = {"kind": "factor", "genome_hash": hash_, "genome": {"expr": "x"},
            "expr_text": f"expr-{hash_}", "describe_text": "中文", "skeleton": "sub(?,?)",
            "mechanism": "gap_premium", "source": "random", "verdict": verdict,
            "created_ts": _TS}
    base.update(over)
    return repo.CandidateRow(**base)


def _iteration(idx: int) -> repo.IterationRow:
    return repo.IterationRow(idx=idx, started_ts=_TS, finished_ts=_TS + 10,
                             budget={"random": 1.0}, stats={"generated": 3},
                             llm={"calls": 0}, note="第 %d 轮" % idx)


async def _commit(sm, run_id, candidates, idx, *, cand_cap=20000, iter_cap=5000):
    async with sm() as session:
        return await repo.commit_iteration(
            session, run_id, candidates=candidates, iteration=_iteration(idx),
            checkpoint_json=json.dumps({"iteration": idx}),
            counters={"tested": len(candidates), "kept": 1},
            candidate_row_cap=cand_cap, iteration_row_cap=iter_cap)


# ============================ run CRUD ============================
async def test_create_list_and_status_transitions():
    sm = await _fresh()
    try:
        async with sm() as session:
            row = await repo.create_run(session, values=_values())
            assert row.status == "running" and row.iteration == 0
            rows, total = await repo.list_runs(session)
            assert total == 1 and rows[0].id == row.id
            await repo.set_run_status(session, row.id, status="paused",
                                      pause_reason="data_unavailable")
            refreshed = await repo.get_run(session, row.id)
            assert refreshed.status == "paused"
            assert refreshed.pause_reason == "data_unavailable"
            _rows, filtered = await repo.list_runs(session, status="running")
            assert filtered == 0
    finally:
        await dispose_engine()


async def test_delete_cascades_to_iterations_and_candidates():
    sm = await _fresh()
    try:
        async with sm() as session:
            run = await repo.create_run(session, values=_values())
        await _commit(sm, run.id, [_candidate("a"), _candidate("b", "rejected")], 1)
        async with sm() as session:
            assert await repo.delete_run(session, run.id) == "deleted"
            assert await repo.get_run(session, run.id) is None
            rows, total = await repo.list_candidates(session, run.id, verdict=None)
            assert rows == [] and total == 0
            assert await repo.list_iterations(session, run.id) == []
            assert await repo.delete_run(session, 999) == "not_found"
    finally:
        await dispose_engine()


# ============================ 单事务原子性（承重） ============================
async def test_commit_iteration_is_atomic_across_three_tables():
    """中途抛异常 ⇒ **三张表都不写**（任意时点被杀只会丢正在进行的那一轮）。"""
    sm = await _fresh()
    try:
        async with sm() as session:
            run = await repo.create_run(session, values=_values())
        # 同批两条同 hash：唯一约束在 flush 时炸，整个事务回滚。
        with pytest.raises(Exception):
            await _commit(sm, run.id, [_candidate("dup"), _candidate("dup")], 1)
        async with sm() as session:
            rows, total = await repo.list_candidates(session, run.id, verdict=None)
            assert total == 0 and rows == []
            assert await repo.list_iterations(session, run.id) == []
            refreshed = await repo.get_run(session, run.id)
            assert refreshed.iteration == 0        # 检查点也没被推进
    finally:
        await dispose_engine()


async def test_commit_iteration_writes_all_three_and_advances_checkpoint():
    sm = await _fresh()
    try:
        async with sm() as session:
            run = await repo.create_run(session, values=_values())
        await _commit(sm, run.id, [_candidate("a"), _candidate("b", "rejected")], 1)
        async with sm() as session:
            rows, total = await repo.list_candidates(session, run.id, verdict=None)
            assert total == 2
            assert {r.verdict for r in rows} == {"kept", "rejected"}
            iterations = await repo.list_iterations(session, run.id)
            assert [i.idx for i in iterations] == [1]
            refreshed = await repo.get_run(session, run.id)
            assert refreshed.iteration == 1 and refreshed.kept_count == 1
            assert json.loads(refreshed.checkpoint_json)["iteration"] == 1
    finally:
        await dispose_engine()


async def test_unique_constraint_dedupes_across_iterations():
    """``UniqueConstraint(run_id, genome_hash)`` 是去重的**物理保证**（D-3）。"""
    sm = await _fresh()
    try:
        async with sm() as session:
            run = await repo.create_run(session, values=_values())
        await _commit(sm, run.id, [_candidate("same")], 1)
        with pytest.raises(Exception):
            await _commit(sm, run.id, [_candidate("same")], 2)
        async with sm() as session:
            hashes = await repo.load_tested_hashes(session, run.id)
            assert hashes == {"same"}
    finally:
        await dispose_engine()


async def test_hashes_are_scoped_per_run():
    sm = await _fresh()
    try:
        async with sm() as session:
            first = await repo.create_run(session, values=_values())
            second = await repo.create_run(session, values=_values(label="第二个"))
        await _commit(sm, first.id, [_candidate("shared")], 1)
        await _commit(sm, second.id, [_candidate("shared")], 1)   # 另一个 run 可复用同 hash
        async with sm() as session:
            assert await repo.load_tested_hashes(session, first.id) == {"shared"}
            assert await repo.load_tested_hashes(session, second.id) == {"shared"}
    finally:
        await dispose_engine()


# ============================ 剪枝（如实上报，绝不静默截断） ============================
async def test_pruning_keeps_all_kept_and_reports_counts():
    sm = await _fresh()
    try:
        async with sm() as session:
            run = await repo.create_run(session, values=_values())
        kept = [_candidate(f"k{i}") for i in range(3)]
        rejected = [_candidate(f"r{i}", "rejected") for i in range(10)]
        pruned = await _commit(sm, run.id, kept + rejected, 1, cand_cap=4)
        assert pruned["prunedCandidates"] == 6      # 10 条 rejected 剪到 4 条
        async with sm() as session:
            kept_rows = await repo.list_kept_candidates(session, run.id)
            assert len(kept_rows) == 3              # kept 一条不剪
            rows, total = await repo.list_candidates(session, run.id, verdict="rejected")
            assert total == 4
    finally:
        await dispose_engine()


async def test_iteration_pruning_reports_counts():
    sm = await _fresh()
    try:
        async with sm() as session:
            run = await repo.create_run(session, values=_values())
        pruned = 0
        for idx in range(1, 6):
            result = await _commit(sm, run.id, [_candidate(f"c{idx}")], idx, iter_cap=3)
            pruned += result["prunedIterations"]
        assert pruned == 2
        async with sm() as session:
            iterations = await repo.list_iterations(session, run.id, limit=20)
            assert [i.idx for i in iterations] == [3, 4, 5]
    finally:
        await dispose_engine()


# ============================ 降级 / 入库标记 / 对账 ============================
async def test_mark_demoted_excludes_from_kept_pool():
    sm = await _fresh()
    try:
        async with sm() as session:
            run = await repo.create_run(session, values=_values())
        await _commit(sm, run.id, [_candidate("a"), _candidate("b")], 1)
        async with sm() as session:
            rows = await repo.list_kept_candidates(session, run.id)
            assert await repo.mark_demoted(session, [rows[0].id]) == 1
            assert len(await repo.list_kept_candidates(session, run.id)) == 1
            assert len(await repo.list_kept_candidates(
                session, run.id, include_demoted=True)) == 2
            assert await repo.mark_demoted(session, []) == 0
    finally:
        await dispose_engine()


async def test_mark_promoted_records_entity_pointer():
    sm = await _fresh()
    try:
        async with sm() as session:
            run = await repo.create_run(session, values=_values())
        await _commit(sm, run.id, [_candidate("a")], 1)
        async with sm() as session:
            row = (await repo.list_kept_candidates(session, run.id))[0]
            updated = await repo.mark_promoted(session, row.id, kind="factor", entity_id=42)
            assert updated.promoted_kind == "factor" and updated.promoted_id == 42
            assert await repo.mark_promoted(session, 999, kind="factor", entity_id=1) is None
    finally:
        await dispose_engine()


async def test_reconcile_orphan_runs_pauses_running_rows():
    """进程启动时内存管理器为空，任何 ``running`` 行必是崩溃残留，绝不留幽灵进行中。"""
    sm = await _fresh()
    try:
        async with sm() as session:
            first = await repo.create_run(session, values=_values())
            second = await repo.create_run(session, values=_values(label="第二个"))
            await repo.set_run_status(session, second.id, status="stopped")
            reconciled = await repo.reconcile_orphan_runs(
                session, paused_reason="process_restart")
            assert reconciled == [first.id]
            refreshed = await repo.get_run(session, first.id)
            assert refreshed.status == "paused"
            assert refreshed.pause_reason == "process_restart"
            assert (await repo.get_run(session, second.id)).status == "stopped"
    finally:
        await dispose_engine()


async def test_candidate_filters_and_pagination():
    sm = await _fresh()
    try:
        async with sm() as session:
            run = await repo.create_run(session, values=_values())
        rows = [_candidate("a", mechanism="gap_premium"),
                _candidate("b", "rejected", reject_reason="F3", mechanism="shadow_pressure"),
                _candidate("c", "rejected", reject_reason="F10", mechanism="gap_premium")]
        await _commit(sm, run.id, rows, 1)
        async with sm() as session:
            _r, kept_total = await repo.list_candidates(session, run.id)
            assert kept_total == 1                      # 默认只回 kept
            _r, by_mech = await repo.list_candidates(session, run.id, verdict=None,
                                                     mechanism="gap_premium")
            assert by_mech == 2
            _r, by_reason = await repo.list_candidates(session, run.id, verdict="rejected",
                                                       reason="F10")
            assert by_reason == 1
            page, total = await repo.list_candidates(session, run.id, verdict=None, limit=2)
            assert len(page) == 2 and total == 3        # total 是真源，不是 len(items)
    finally:
        await dispose_engine()
