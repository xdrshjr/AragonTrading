"""存量成果一次性回填（discovery-asset-ledger §2.11 · P1-9 / D-29）。

没有回填，本轮交付的是一个「对老用户永远空、对新用户要等几天才有内容」的分区——用户点开
新分区看到空表 + 「台账为空但有 run 在跑」的空态，正好把「功能上线」读成「功能坏了」。

五条纪律各一条用例：幂等 / 有界 / fail-open / 口径退化如实标注 / ``next_check_ts`` 错峰。
"""
from __future__ import annotations

import json

from sqlalchemy import delete

from app.config import Settings
from app.db.models import DiscoveryAsset, DiscoveryCandidate, DiscoveryIteration, DiscoveryRun
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.discovery import asset_repositories as asset_repo
from app.discovery import assets
from app.discovery import repositories as repo

TS0 = 1_700_000_000
EXPRS = ["sub(ts_mean(overnight,20),delta(amplitude,5))", "ts_std(ret,30)",
         "ts_zscore(volume,20)", "roc(close,45)"]


async def _fresh():
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        for model in (DiscoveryAsset, DiscoveryCandidate, DiscoveryIteration, DiscoveryRun):
            await session.execute(delete(model))
        await session.commit()
    return sm


async def _seed_legacy(sm, exprs: list[str]) -> int:
    """造一个「本功能上线前」的 run：只有 kept 候选，没有任何资产行。"""
    async with sm() as session:
        run = await repo.create_run(session, values=repo.RunValues(
            label="老任务", inst_id="BTC-USDT-SWAP", bar="15m", symbols=["BTC-USDT-SWAP"],
            config={"isRatio": 0.7, "windowBars": 4000, "thresholds": {"minAbsIc": 0.05}},
            checkpoint={"dataWindow": {"startTs": TS0 - 4000 * 900, "endTs": TS0}},
            started_ts=TS0))
        await repo.commit_iteration(
            session, run.id,
            candidates=[repo.CandidateRow(
                kind="factor", genome_hash=f"h{index}", genome={}, expr_text=expr,
                describe_text="历史成果", skeleton="sk", mechanism="gap_premium",
                source="mutate", verdict="kept",
                metrics={"direction": "long", "horizon": 6, "is": {"rankIc": 0.05}},
                trials_at_accept=10, created_ts=TS0 + index)
                for index, expr in enumerate(exprs)],
            iteration=repo.IterationRow(idx=1, started_ts=TS0, finished_ts=TS0, budget={},
                                        stats={}, llm={}),
            checkpoint_json=json.dumps({"dataWindow": {"startTs": TS0 - 4000 * 900,
                                                       "endTs": TS0}}),
            counters={}, candidate_row_cap=100, iteration_row_cap=100)
    return int(run.id)


async def test_backfill_fills_the_ledger_and_marks_the_degraded_window() -> None:
    """回填行必须 ``backfilled=True`` 且 ``mined_window.splitTs == 0``。

    老候选的切点**无法重算**（当时的 df 已经不在了）。**不许**拿今天的窗口冒充当时的窗口——
    那正是 AL-3 要防的「换分母」。
    """
    sm = await _fresh()
    try:
        await _seed_legacy(sm, EXPRS)
        async with sm() as session:
            stats = await assets.backfill_assets(session, Settings())
            rows, total = await asset_repo.list_assets(session, asset_repo.AssetQuery())
        assert stats.created == len(EXPRS) and total == len(EXPRS)
        assert all(row.backfilled for row in rows)
        assert all(asset_repo.loads(row.mined_window_json, {})["splitTs"] == 0
                   for row in rows)
        assert all(asset_repo.loads(row.mined_window_json, {})["endTs"] == TS0
                   for row in rows)
    finally:
        await dispose_engine()


async def test_backfill_is_idempotent() -> None:
    """跑两次结果相同（幂等靠 ``asset_key`` 唯一索引 + 同一条 upsert 路径）。"""
    sm = await _fresh()
    try:
        await _seed_legacy(sm, EXPRS)
        async with sm() as session:
            await assets.backfill_assets(session, Settings())
            second = await assets.backfill_assets(session, Settings())
            _rows, total = await asset_repo.list_assets(session, asset_repo.AssetQuery())
        assert total == len(EXPRS)
        assert second.created == 0 and second.merged == 0
    finally:
        await dispose_engine()


async def test_backfill_staggers_the_first_check_within_one_interval() -> None:
    """错峰摊进一个 ``interval``：否则上线瞬间几千条资产的首检会一起冲 OKX（R-17）。"""
    sm = await _fresh()
    try:
        await _seed_legacy(sm, EXPRS)
        settings = Settings()
        async with sm() as session:
            await assets.backfill_assets(session, settings)
            rows, _total = await asset_repo.list_assets(session, asset_repo.AssetQuery())
        due = sorted(int(row.next_check_ts) for row in rows)
        assert len(set(due)) > 1                      # 不是全部相同
        assert due[-1] - due[0] < int(settings.discovery_health_interval_s)
    finally:
        await dispose_engine()


async def test_backfill_is_bounded_and_counts_what_it_skipped() -> None:
    """超上限即停止新建、**计数并 log**，绝不静默截断、绝不删旧。"""
    sm = await _fresh()
    try:
        await _seed_legacy(sm, EXPRS)
        settings = Settings()
        object.__setattr__(settings, "discovery_health_max_assets", 2)
        async with sm() as session:
            stats = await assets.backfill_assets(session, settings)
            _rows, total = await asset_repo.list_assets(session, asset_repo.AssetQuery())
        assert total == 2
        assert stats.created == 2 and stats.skipped == len(EXPRS) - 2
    finally:
        await dispose_engine()


async def test_backfill_failure_never_blocks_startup(monkeypatch) -> None:
    """回填抛错只 warning、不阻断启动（``main._backfill_discovery_assets`` 的 fail-open）。"""
    from app import main

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(assets, "backfill_assets", _boom)
    await main._backfill_discovery_assets()        # 不抛即通过


async def test_orphan_candidates_without_a_run_are_skipped() -> None:
    """run 已被删除的候选自然回填不到——本轮不负责考古（空态文案已如实说明）。"""
    sm = await _fresh()
    try:
        run_id = await _seed_legacy(sm, EXPRS[:1])
        async with sm() as session:
            await session.execute(delete(DiscoveryRun).where(DiscoveryRun.id == run_id))
            await session.commit()
            stats = await assets.backfill_assets(session, Settings())
        assert stats.created == 0
    finally:
        await dispose_engine()
