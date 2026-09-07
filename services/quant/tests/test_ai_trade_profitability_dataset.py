"""收益总体的真实 SQLite 断面与归并契约（合成数据）。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import event

from app.ai_auto.track_record_service import build_profitability
from app.config import get_settings
from app.db import repositories as repo
from app.db.models import StrategyRun
from app.db.session import get_sessionmaker
from tests.test_ai_trade_profitability_api import _add_run, _add_trip, _reset


async def test_incomplete_symbols_remain_in_the_population():
    sid = await _reset()
    async with get_sessionmaker()() as session:
        for symbol, count, known in (
            ("SOL", 2, True),
            ("BTC", 2, False),
            ("ETH", 1, False),
        ):
            run_id = await _add_run(
                session,
                strategy_id=sid,
                group_id=f"aiprof_{symbol}",
                inst_id=f"{symbol}-USDT-SWAP",
            )
            for index in range(count):
                await _add_trip(
                    session,
                    run_id,
                    entry=100,
                    exit_px=101,
                    pnl=1,
                    fee_known=known,
                    ts=100 + index * 200,
                )
        wire = await build_profitability(
            session, get_settings(), repo.TripQuery()
        )
    coverage = wire.get("coverage", {})
    assert coverage.get("totalTrips") == 5
    assert coverage["netEligibleTrips"] == 2
    assert coverage["excludedTrips"] == 3
    assert coverage["netCoverage"] == 0.4
    assert len(coverage["byInstId"]) == 3
    assert wire["verdict"]["code"] == "insufficient_evidence"


async def test_flat_price_preserves_contract_notional_and_risk():
    sid = await _reset()
    async with get_sessionmaker()() as session:
        run_id = await _add_run(
            session, strategy_id=sid, group_id="aiprof_flat"
        )
        run = await session.get(StrategyRun, run_id)
        config = json.loads(run.config_json)
        config["ctVal"] = 0.1
        run.config_json = json.dumps(config)
        await session.commit()
        await _add_trip(
            session,
            run_id,
            entry=100,
            exit_px=100,
            pnl=0,
            fee=0.5,
            stop=95,
            take=110,
        )
        rows = await repo.load_profitability_trips(session, repo.TripQuery())
    assert rows[0]["notionalUsd"] == 100
    assert rows[0]["realizedCostBps"] == 100
    assert rows[0]["realizedNetBps"] == -100
    assert rows[0]["rMultiple"] == -0.2


async def test_empty_runs_obey_all_filters_and_preserve_since_basis():
    sid = await _reset()
    async with get_sessionmaker()() as session:
        keep = await _add_run(session, strategy_id=sid, group_id="aiprof_keep")
        drop = await _add_run(
            session,
            strategy_id=sid,
            group_id="aiprof_drop",
            inst_id="ETH-USDT-SWAP",
            preset="aggressive",
        )
        run = await session.get(StrategyRun, keep)
        run.group_started_ts, run.start_ts = 2000, 100
        config = json.loads(run.config_json)
        config["tradingHorizon"] = {"profile": "short"}
        run.config_json = json.dumps(config)
        other = await session.get(StrategyRun, drop)
        other.group_started_ts, other.start_ts = 100, 3000
        await session.commit()
        dataset = await repo.load_profitability_dataset(
            session,
            repo.TripQuery(
                since_ts=1500, preset="balanced", horizon_profile="short"
            ),
        )
        wire = await build_profitability(
            session, get_settings(), repo.TripQuery(since_ts=1500)
        )
    assert len(dataset.run_dimensions) == 1
    assert dataset.run_dimensions[0]["runId"] == keep
    assert wire["coverage"]["totalTrips"] == 0
    assert wire["coverage"]["netCoverage"] is None
    assert len(wire["coverage"]["byInstId"]) == 1
    assert wire["scope"]["sinceField"] == "group_started_ts_or_start_ts"


async def test_unknown_basis_has_one_reason_and_original_trade_is_untouched():
    from sqlalchemy import select
    from app.db.models import Trade

    sid = await _reset()
    async with get_sessionmaker()() as session:
        run = await _add_run(
            session, strategy_id=sid, group_id="aiprof_basis", cost_basis=None
        )
        await _add_trip(
            session, run, entry=100, exit_px=101, pnl=1, fee_known=False
        )
        statement = (
            select(Trade.explain_json)
            .where(Trade.run_id == run)
            .order_by(Trade.id)
        )
        before = (await session.execute(statement)).all()
        dataset = await repo.load_profitability_dataset(
            session, repo.TripQuery()
        )
        after = (await session.execute(statement)).all()
    assert before == after
    assert len(dataset.samples) == 1
    row = dataset.samples[0]
    assert (
        row["exclusionReason"] == "basis_unknown"
        and row["netEligible"] is False
    )
    assert row["netPnlUsd"] is None


async def test_structural_events_are_distinct_but_duplicates_are_not_counted():
    from app.db.models import Trade

    sid = await _reset()
    async with get_sessionmaker()() as session:
        run = await _add_run(session, strategy_id=sid, group_id="aiprof_issues")
        for identifier in ("one", "one", "two"):
            session.add(
                Trade(
                    run_id=run,
                    ts=100,
                    side="close",
                    qty=1,
                    price=100,
                    fee=0,
                    pnl=0,
                    reason="",
                    explain_json=json.dumps(
                        {
                            "meta": {
                                "accountingEventId": identifier,
                                "feeBasis": "measured",
                            }
                        }
                    ),
                )
            )
        await session.commit()
        wire = await build_profitability(
            session, get_settings(), repo.TripQuery()
        )
    assert wire["coverage"]["structuralIssueCount"] == 2
    assert wire["coverage"]["totalTrips"] == 0
    assert wire["verdict"]["code"] == "insufficient_evidence"
    for key in ("byInstId", "byBar", "byPreset"):
        row = wire["slices"][key][0]
        assert row["coverage"]["structuralIssueCount"] == 2
        assert row["totalTrips"] == row["netEligibleTrips"] == 0
    assert wire["slices"]["byExitSource"] == []


async def test_normal_open_epoch_is_separate_from_completed_evidence():
    from app.db.models import Trade

    sid = await _reset()
    async with get_sessionmaker()() as session:
        run = await _add_run(session, strategy_id=sid, group_id="aiprof_open")
        await _add_trip(session, run, entry=100, exit_px=101, pnl=1)
        session.add(
            Trade(
                run_id=run,
                ts=300,
                side="long",
                qty=1,
                price=100,
                fee=0,
                pnl=0,
                reason="",
                explain_json=json.dumps({"meta": {"feeBasis": "measured"}}),
            )
        )
        await session.commit()
        wire = await build_profitability(
            session,
            get_settings().model_copy(
                update={"ai_trade_profitability_min_trips": 1}
            ),
            repo.TripQuery(),
        )
    assert wire["coverage"]["openEpochCount"] == 1
    assert wire["coverage"]["structuralIssueCount"] == 0
    assert wire["coverage"]["totalTrips"] == 1
    assert wire["conclusive"] is True


async def test_snapshot_identity_is_stable_and_depends_on_read_economics(
    monkeypatch,
):
    from app.db import ai_profitability_repositories as source

    sid = await _reset()
    async with get_sessionmaker()() as session:
        run = await _add_run(
            session, strategy_id=sid, group_id="aiprof_identity"
        )
        await _add_trip(session, run, entry=100, exit_px=101, pnl=1)
        monkeypatch.setattr(source.time, "time", lambda: 2000)
        first = await build_profitability(
            session, get_settings(), repo.TripQuery()
        )
        monkeypatch.setattr(source.time, "time", lambda: 3000)
        same = await build_profitability(
            session, get_settings(), repo.TripQuery()
        )
        await _add_trip(
            session, run, entry=100, exit_px=100, pnl=0, fee_known=False
        )
        changed = await build_profitability(
            session, get_settings(), repo.TripQuery()
        )
    assert first["snapshotId"] == same["snapshotId"] != changed["snapshotId"]
    assert first["asOfTs"] == 2000 and same["asOfTs"] == 3000


@pytest.mark.parametrize(
    "phase", ["before_cursor_execute", "after_cursor_execute"]
)
async def test_one_select_is_a_coherent_snapshot_without_taking_transaction(
    phase,
):
    import sqlite3

    sid = await _reset()
    async with get_sessionmaker()() as session:
        run = await _add_run(session, strategy_id=sid, group_id="aiprof_atomic")
        await _add_trip(session, run, entry=100, exit_px=101, pnl=1, fee=0.5)
        engine = session.bind.sync_engine
        database = engine.url.database
        with sqlite3.connect(database) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
        statements = []
        switched = False

        def switch_snapshot(
            connection, cursor, statement, parameters, context, executemany
        ):
            nonlocal switched
            statements.append(statement)
            if switched or "LEFT OUTER JOIN trades" not in statement:
                return
            switched = True
            with sqlite3.connect(database) as writer:
                raw = writer.execute(
                    "SELECT config_json FROM strategy_runs WHERE id=?", (run,)
                ).fetchone()[0]
                config = {**json.loads(raw), "costBasis": "net"}
                writer.execute(
                    "UPDATE strategy_runs SET config_json=? WHERE id=?",
                    (json.dumps(config), run),
                )
                writer.execute(
                    "UPDATE trades SET pnl=10, fee=1 WHERE run_id=? AND side='close'",
                    (run,),
                )

        event.listen(engine, phase, switch_snapshot)
        transaction_events = []
        on_commit = lambda *args: transaction_events.append("commit")
        on_rollback = lambda *args: transaction_events.append("rollback")
        event.listen(engine, "commit", on_commit)
        event.listen(engine, "rollback", on_rollback)
        try:
            dataset = await repo.load_profitability_dataset(
                session, repo.TripQuery()
            )
        finally:
            event.remove(engine, phase, switch_snapshot)
            event.remove(engine, "commit", on_commit)
            event.remove(engine, "rollback", on_rollback)
    assert switched and len(statements) == 1
    assert statements[0].lstrip().upper().startswith("SELECT")
    assert transaction_events == []
    assert dataset.samples[0]["netPnlUsd"] == (
        10 if phase == "before_cursor_execute" else 0
    )


async def test_frozen_fee_comparison_and_raw_amounts_reach_history():
    sid = await _reset()
    async with get_sessionmaker()() as session:
        run_id = await _add_run(
            session, strategy_id=sid, group_id="aiprof_fees"
        )
        run = await session.get(StrategyRun, run_id)
        run.config_json = json.dumps(
            {**json.loads(run.config_json), "ctVal": 1}
        )
        await session.commit()
        await _add_trip(
            session,
            run_id,
            entry=100,
            exit_px=101,
            pnl=10,
            fee=0.5,
            meta={
                "estFeeBps": 10,
                "estCostBps": 16,
                "modelVersion": 3,
                "comparisonVersion": 1,
                "notionalUsd": 999,
                "grossPnlUsd": 999,
            },
        )
        wire = await build_profitability(
            session, get_settings(), repo.TripQuery()
        )
    cost = wire["calibration"]["costModel"]
    assert cost["n"] == 1
    assert cost["medianFeeErrorBps"] == 0
    assert cost["medianEstTotalCostBps"] == 16
    assert wire["pooled"]["grossPnlUsd"] == 10


async def test_invalid_run_dimensions_are_unknown_and_do_not_break_api():
    sid = await _reset()
    async with get_sessionmaker()() as session:
        run_id = await _add_run(session, strategy_id=sid, group_id="aiprof_invalid_dims")
        run = await session.get(StrategyRun, run_id)
        run.config_json = json.dumps({"costBasis": "gross", "ctVal": 1,
            "bar": float("nan"), "tradingHorizon": {"profile": float("inf")}})
        run.metrics_json = json.dumps({"aiStats": {"disciplinePreset": []}})
        await session.commit()
        await _add_trip(session, run_id, entry=100, exit_px=101, pnl=1)
        wire = await build_profitability(session, get_settings(), repo.TripQuery())
    assert wire["coverage"]["totalTrips"] == 1
    for key in ("byBar", "byPreset", "byHorizonProfile"):
        assert wire["slices"][key][0]["key"] == "unknown"
