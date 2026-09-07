"""一致性状态的真实 SQLite 并发写与响应契约。"""
from __future__ import annotations

import asyncio

import pytest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ai_auto.decision_memory import EVIDENCE_VERSION
from app.db import ai_trade_state_repositories as repo
from app.db.models import Base


async def test_settlement_patch_merges_owned_keys_and_rejects_stale_revision(
        tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'epoch.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    member = {"instId": "BTC", "runId": 1, "runtimeIntegrity": {
        "pendingOpenAttempt": {"attemptId": "fixed"},
        "entryQuota": {"used": 2}, "unknownFuture": {"keep": True}}}
    context = {"schemaVersion": 1, "accountId": 7, "runId": 1,
               "instId": "BTC", "epochKey": "epoch-1", "posId": "p"}
    patch = {"accountId": 7, "runId": 1, "expectedEpochKey": None,
             "expectedSettlementRevision": 0,
             "runtimeIntegrity": {"settlementContext": context}}
    try:
        async with factory() as db:
            await repo.upsert_group_state(db, repo.GroupStateWrite(
                "group", "running", account_id=7, members=[member]))
            assert await repo.patch_runtime_integrity(db, "group", "BTC", patch)
            row = (await repo.load_group_state(db, "group"))["members"][0]
            runtime = row["runtimeIntegrity"]
            assert runtime["pendingOpenAttempt"] == {"attemptId": "fixed"}
            assert runtime["entryQuota"] == {"used": 2}
            assert runtime["unknownFuture"] == {"keep": True}
            assert runtime["settlementStateRevision"] == 1
            assert not await repo.patch_runtime_integrity(
                db, "group", "BTC", patch)
            await repo.patch_member_state(db, "group", "BTC", member)
            clear = {**patch, "expectedEpochKey": "epoch-1",
                     "expectedSettlementRevision": 1,
                     "runtimeIntegrity": {"settlementContext": None}}
            assert await repo.patch_runtime_integrity(db, "group", "BTC", clear)
            row = (await repo.load_group_state(db, "group"))["members"][0]
            assert row["runtimeIntegrity"]["settlementContext"] is None
            assert row["runtimeIntegrity"]["settlementStateRevision"] == 2
            assert "expectedEpochKey" not in row
    finally:
        await engine.dispose()


@pytest.mark.parametrize("conflict", ["accountId", "runId", "instId"])
async def test_settlement_patch_rejects_context_scope(tmp_path, conflict):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scope.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    context = {"schemaVersion": 1, "accountId": 7, "runId": 1,
               "instId": "BTC", "epochKey": "epoch"}
    context[conflict] = "wrong"
    try:
        async with factory() as db:
            await repo.upsert_group_state(db, repo.GroupStateWrite(
                "group", "running", account_id=7,
                members=[{"instId": "BTC", "runId": 1}]))
            assert not await repo.patch_runtime_integrity(db, "group", "BTC", {
                "accountId": 7, "runId": 1, "expectedEpochKey": None,
                "expectedSettlementRevision": 0,
                "runtimeIntegrity": {"settlementContext": context}})
    finally:
        await engine.dispose()


async def test_stale_group_snapshot_preserves_pending_and_other_member(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    members = [{"instId": "BTC", "runId": 1, "custom": 7},
               {"instId": "ETH", "runId": 2}]
    write = repo.GroupStateWrite("group", "running", members=members)
    try:
        async with factory() as db:
            await repo.upsert_group_state(db, write)

        async def reserve(inst_id, run_id):
            async with factory() as db:
                return await repo.patch_runtime_integrity(db, "group", inst_id, {
                    "runId": run_id, "runtimeIntegrity": {
                        "schemaVersion": 2,
                        "pendingOpenAttempt": {"attemptId": f"{run_id}:unique"},
                    }})

        assert all(await asyncio.gather(reserve("BTC", 1), reserve("ETH", 2)))
        async with factory() as db:
            await repo.upsert_group_state(db, write)
            await repo.patch_group_state(db, "group", {"status": "ended"})
            row = await repo.load_group_state(db, "group")
            assert row["members"][0]["custom"] == 7
            assert all(m["runtimeIntegrity"]["pendingOpenAttempt"]
                       for m in row["members"])
            assert await repo.load_pending_groups(db)
            hydrated = await repo.load_paused_group_brief(db)
            assert hydrated["groupId"] == "group"
            assert hydrated["members"][0]["runtimeIntegrity"][
                "pendingOpenAttempt"]
            assert not await repo.patch_runtime_integrity(db, "group", "BTC", {
                "runId": 99, "runtimeIntegrity": {"schemaVersion": 2}})
            assert await repo.delete_group_state(db, "group") == 0
            await repo.patch_runtime_integrity(db, "group", "BTC", {
                "runId": 1, "runtimeIntegrity": {
                    "schemaVersion": 2, "pendingOpenAttempt": None,
                    "adoptedOrder": {"resting": {"cl_ord_id": "accepted"}}}})
            await repo.remove_member_state(db, "group", "BTC", 1)
            retained = await repo.load_group_state(db, "group")
            btc = next(m for m in retained["members"] if m["instId"] == "BTC")
            assert btc["state"] == "stopped"
            assert btc["runtimeIntegrity"]["adoptedOrder"]["resting"]
    finally:
        await engine.dispose()


def test_pending_identity_cannot_override_position_safety():
    from types import SimpleNamespace
    from app.ai_auto.resume import _pending_position_matches
    from app.ai_auto import pause

    pending = SimpleNamespace(cl_ord_id="fixed", order={
        "direction": "long", "td_mode": "cross"})
    state = {"state": "filled", "accFillSz": "2"}
    member = {"actual": {"pos": "2", "posSide": "long"}}
    assert _pending_position_matches(member, pending, state, {})
    for patch in (
        {"blockedReason": "backstop_required"},
        {"verdict": pause.VERDICT_FOREIGN_POSITION},
        {"actual": {"pos": "-2", "posSide": "net"}},
        {"actual": {"pos": "2", "mgnMode": "isolated"}},
        {"pendingOrders": [{"clOrdId": "another"}]},
    ):
        assert not _pending_position_matches(
            member | patch, pending, state, {})


def test_session_schema_preserves_integrity_and_rules():
    from app.schemas import AiTradeSessionRow
    from tests.test_ai_trade_funnel_session import _session

    session = _session()
    wire = session.info()
    encoded = AiTradeSessionRow.model_validate(wire).model_dump()
    assert encoded["integrity"] == wire["integrity"]
    assert encoded["integrity"]["rules"]["risk"]
    assert encoded["integrity"]["funnel"]["schemaVersion"] == 2
    assert encoded["integrity"]["economics"]["fundingBasis"] == "unknown"


def test_diagnosis_distinguishes_acceptance_from_unknown_and_rejection():
    from app.ai_auto.integrity_view import _diagnosis
    from tests.test_ai_trade_funnel_session import _session

    session = _session()
    assert _diagnosis(session, {"ack": "accepted"})["code"] == (
        "accepted_pending")
    assert _diagnosis(session, {"ack": "none"})["code"] == (
        "acceptance_unknown")
    session._last_decision = {"executed": {"executionReceipt": {
        "status": "rejected", "reason": "insufficient_balance"}}}
    assert "保证金不足" in _diagnosis(session, None)["message"]


async def test_completed_sizing_veto_survives_status_and_schema():
    from app.ai_auto.decider import TradeDecision
    from app.ai_auto.integrity_view import _diagnosis
    from app.schemas import AiTradeSessionRow
    from tests.test_ai_trade_funnel_session import _session

    session = _session()
    session._round_sizing = {"floorVerdict": "solved_zero"}
    session._round_facts = {"factsHash": "frozen-round-hash"}
    await session._emit_decision(
        1, 100, TradeDecision("hold", None, .9, "仓位求解为零"), [],
        {"ok": True, "action": "hold", "traded": False})
    # 下一轮的瞬时槽清空后，仍应准确解释最近一轮的系统阻断。
    session._round_sizing = None
    diagnosis = _diagnosis(session, None)
    assert diagnosis["code"] == "sizing_zeroed"
    assert "仓位求解" in diagnosis["message"]
    wire = AiTradeSessionRow.model_validate(session.info()).model_dump()
    last = wire["lastDecision"]
    assert last["sizingVerdict"]["floorVerdict"] == "solved_zero"
    assert last["factsHash"] == "frozen-round-hash"


async def test_history_response_preserves_group_unknown_coverage(tmp_path):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from app.db import repositories
    from app.db.ai_auto_repositories import get_ai_trade_group_detail
    from app.schemas import AiTradeHistoryDetailOut, trade_out

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'history.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _seed_integrity_history(factory, repositories)
        async with factory() as db:
            raw = await get_ai_trade_group_detail(db, "integrity_history")
        for member in raw["members"]:
            member["trades"] = [trade_out(t) for t in member["trades"]]
        app = FastAPI()

        @app.get("/history", response_model=AiTradeHistoryDetailOut)
        async def history():
            return raw

        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            response = await client.get("/history")
        assert response.status_code == 200
        wire = response.json()
        assert wire["summary"]["economics"] == raw["summary"]["economics"]
        assert wire["summary"]["roundTripCount"] == 3
        economics = wire["summary"]["economics"]
        assert economics["roundTripCount"] == 3
        assert economics["completeFeeTripCount"] == 2
        assert economics["feeUsd"] is None
        assert economics["fundingBasis"] == "unknown"
        assert len(wire["members"][0]["trades"]) == 4
    finally:
        await engine.dispose()


async def _seed_integrity_history(factory, repositories):
    async with factory() as db:
        for index, instrument in enumerate(("BTC", "ETH")):
            run = await repositories.create_run(
                db, strategy_id=1, inst_id=f"{instrument}-USDT-SWAP",
                bar="1H", mode="ai-trade", status="stopped",
                config={"costBasis": "gross", "budgetUsd": 100,
                        "ctVal": 1, "strategyNames": ["测试"]},
                group_id="integrity_history", group_started_ts=100,
                group_total_budget_usd=200, start_ts=100)
            for pair in range(2 - index):
                for leg, side in enumerate(("long", "close")):
                    meta = ({"feeBasis": "measured", "feeKnown": True,
                             "feeCurrency": "USDT"} if index == 0 else {})
                    await repositories.append_live_trade(db, run.id, {
                        "ts": 100 + pair * 2 + leg, "side": side,
                        "price": 100 + 10 * leg, "qty": 1, "fee": 0.1,
                        "pnl": 10 * leg, "positionAfter": 1 - leg,
                        "explain": {"meta": meta}})


async def test_raw_trade_is_idempotent_even_with_legacy_bad_json(
        tmp_path, monkeypatch):
    from sqlalchemy import select
    from app.db.models import Trade
    from tests.test_ai_trade_funnel_session import _session

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'raw.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session = _session()
    monkeypatch.setattr("app.ai_auto.session.get_sessionmaker", lambda: factory)
    trade = {"ts": 1, "side": "long", "qty": 1, "price": 100,
             "fee": 0, "pnl": 0, "positionAfter": 1,
             "explain": {"meta": {"accountingEventId": "new-fixed-id"}}}
    try:
        async with factory() as db:
            db.add(Trade(run_id=1, ts=0, side="close", price=90,
                         qty=1, fee=0, pnl=-10, position_after=0,
                         explain_json="{旧损坏记录"))
            await db.commit()
        await session._persist_trade(trade)
        await session._persist_trade(trade)
        assert not session._trade_persist_failed
        async with factory() as db:
            rows = (await db.execute(select(Trade))).scalars().all()
        assert len(rows) == 2
        assert rows[0].explain_json == "{旧损坏记录"
    finally:
        await engine.dispose()


async def test_partial_close_keeps_epoch_and_full_missing_fee_closes_it(
        monkeypatch):
    from unittest.mock import AsyncMock, Mock
    from tests.test_ai_trade_funnel_session import _session

    session = _session()
    monkeypatch.setattr(session, "_persist_trade", AsyncMock())
    monkeypatch.setattr(session, "_persist_state_position", AsyncMock())
    finished = Mock()
    monkeypatch.setattr(session._horizon, "note_close", finished)
    base = {"ts": 100, "price": 100, "fee": 0, "pnl": 0,
            "feeBasis": "measured", "feeKnown": True}
    opened = {**base, "side": "long", "qty": 10, "positionAfter": 10}
    await session._dispatch([{"type": "trade", "data": opened}])
    session._known_cl_ord_ids = ["current-order"]
    partial = {**base, "side": "close", "qty": 4, "positionAfter": 6}
    await session._dispatch([{"type": "trade", "data": partial}])
    assert session._position_seq == 0
    assert session._known_cl_ord_ids == ["current-order"]
    finished.assert_not_called()
    complete = {**base, "side": "close", "qty": 6,
                "feeKnown": False, "positionAfter": 0}
    await session._dispatch([{"type": "trade", "data": complete}])
    assert session._position_seq == 1
    assert session._known_cl_ord_ids == []
    finished.assert_called_once()
    await session._dispatch([{"type": "trade", "data": complete}])
    assert session._position_seq == 1
    finished.assert_called_once()


def test_settlement_validation_is_same_source_for_facts_and_status():
    from app.ai_auto.integrity_view import build_session_facts
    from app.schemas import AiTradeSessionRow
    from tests.test_ai_trade_funnel_session import _session

    session = _session()
    validation = {"schemaVersion": 1, "status": "matched", "reason": None,
                  "detailReason": None, "epochKey": "first",
                  "settlementKey": "official",
                  "feeAdjustment": {"kind": "aggregate_residual",
                      "exchangeTripFeeUsd": 0.2, "bookedLocalFeeUsd": 0.5,
                      "residualFeeUsd": -0.3, "feeBasis": "estimated"}}
    session.executor.last_settlement_validation = validation
    status = AiTradeSessionRow.model_validate(session.info()).model_dump()
    assert status["integrity"]["settlementValidation"] == validation
    facts = build_session_facts(session, {"round": 1})
    assert facts["settlementValidation"] == validation
    assert status["integrity"]["memory"]["evidenceVersion"] == EVIDENCE_VERSION


async def test_observation_save_failure_blocks_open_and_retries_real_sqlite(
        tmp_path, monkeypatch):
    from sqlalchemy import event
    from unittest.mock import AsyncMock
    from app.live_auto.settlement_identity import IdentityConfig
    from tests.test_ai_trade_funnel_session import _session

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'failure.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session = _session()
    session.group_id = "group"
    executor = session.executor
    executor._settlement_fn = AsyncMock(return_value=[])
    executor.configure_settlement_identity(IdentityConfig(
        1, 1, session.inst_id, {"tickSz": .1, "marginMode": "cross"}))
    monkeypatch.setattr("app.ai_auto.session.get_sessionmaker", lambda: factory)
    failed = []

    def reject_update(conn, cursor, statement, parameters, context, many):
        if statement.startswith("UPDATE ai_trade_group_states") and not failed:
            failed.append(True)
            raise RuntimeError("测试注入观察写失败")

    try:
        async with factory() as db:
            await repo.upsert_group_state(db, repo.GroupStateWrite(
                "group", "running", account_id=1,
                members=[session.state_member_row()]))
        executor.settlement_identity.record_trade({
            "id": 1, "side": "long", "qty": 1, "price": 100,
            "wallTs": 100, "fee": 0, "feeBasis": "measured"})
        event.listen(engine.sync_engine, "before_cursor_execute", reject_update)
        assert not await session._persist_settlement_identity()
        assert session._prepare_open_context() == "attempt_persist_failed"
        assert executor.settlement_identity.context() is not None
        await session._sync_execution_integrity()
        assert not session._execution_state_persist_failed
        async with factory() as db:
            member = (await repo.load_group_state(db, "group"))["members"][0]
        assert member["runtimeIntegrity"]["settlementStateRevision"] == 1
        assert member["runtimeIntegrity"]["settlementContext"]["epochKey"]
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", reject_update)
        await engine.dispose()


async def test_unknown_realized_keeps_session_outputs_unknown(monkeypatch):
    from unittest.mock import AsyncMock, Mock
    from tests.test_ai_trade_funnel_session import _session

    session = _session()
    session.executor.realized_pnl_usd = None
    session.take_profit_usd = session.stop_loss_usd = 10
    monkeypatch.setattr(session, "stop", AsyncMock())
    monkeypatch.setattr(session, "_push", AsyncMock())
    note_notional = Mock()
    monkeypatch.setattr(session._sizing, "note_notional", note_notional)
    await session._maybe_session_exit(100, 100)
    session.stop.assert_not_called()
    await session._emit_equity(100, 100)
    note_notional.assert_called_once_with(session.executor, 100)
    assert not session._equity
    metrics = session._build_run_metrics()
    assert metrics["totalReturn"] is None
    assert metrics["avgPnl"] is None
    payload = session._build_decision_payload(1, 100, 100, [], {})
    assert payload["sessionPnlUsd"] is None


def _stopping_settlement_session(monkeypatch, rows):
    from unittest.mock import AsyncMock
    from tests.test_ai_trade_funnel_session import _session
    from tests.test_external_close_settlement import _make

    session = _session()
    executor, calls = _make(settlement_rows=rows)
    session.executor = executor
    session.inst_id = executor.inst_id
    for name in ("_dispatch", "_finalize_run", "_notify_stopped", "_push"):
        monkeypatch.setattr(session, name, AsyncMock())
    monkeypatch.setattr(session._cost, "reconcile_tick", AsyncMock())
    for name in ("flush", "persist_harvest_gap"):
        monkeypatch.setattr(session._experience, name, AsyncMock())
    sync = AsyncMock(wraps=session._sync_execution_integrity)
    monkeypatch.setattr(session, "_sync_execution_integrity", sync)
    return session, executor, calls


@pytest.mark.parametrize("flatten", [False, True])
async def test_stopped_session_retries_settlement_before_finalizing(
        monkeypatch, flatten):
    from tests.test_external_close_settlement import (
        _MARK_PX, _TS, _settlement_row,
    )

    wall = [_TS + 10]
    monkeypatch.setattr("app.live_auto.executor.time.time", lambda: wall[0])
    rows = []
    session, executor, calls = _stopping_settlement_session(monkeypatch, rows)
    executor._begin_settling(_MARK_PX, int(wall[0]))
    pending = executor._settling
    first = await session.stop(flatten=flatten)
    assert session.state == "stopped"
    assert first["tradePersistenceFailed"] is True
    assert executor._settling is pending
    session._finalize_run.assert_not_awaited()

    wall[0] += 1
    await session.stop(flatten=flatten)
    assert executor._settling is pending
    session._finalize_run.assert_not_awaited()
    session._notify_stopped.assert_not_awaited()
    sync_count = session._sync_execution_integrity.await_count
    rows.append(_settlement_row())
    wall[0] = pending.deadline + 1
    result = await session.stop(flatten=flatten)
    assert executor._settling is None
    assert executor.projected == "flat"
    assert executor.last_settlement_validation["status"] == "matched"
    assert result["ok"] is True
    assert session._sync_execution_integrity.await_count > sync_count
    session._finalize_run.assert_awaited_once()
    session._notify_stopped.assert_awaited_once()
    emitted = [event for call in session._dispatch.await_args_list
               for event in call.args[0] if event.get("type") == "trade"]
    assert len(emitted) == 1
    assert calls["closes"] == []
