"""终审回归：提交回执、取消、会计缺口和完整往返消费。"""
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.ai_auto import resume
from app.ai_auto.drawdown_brake import BrakeRule
from app.db import ai_trade_state_repositories as state_repo
from app.db import repositories
from app.db.models import Base, Strategy, StrategyRun
from app.live_auto.settlement_identity import IdentityConfig
from tests.test_ai_trade_funnel_session import _session
from tests.test_ai_trade_resume_settlement import _new_member


@pytest.fixture
async def recovery_database(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    for module in ("app.ai_auto.resume", "app.ai_auto.session"):
        monkeypatch.setattr(f"{module}.get_sessionmaker", lambda: factory)
    async with factory() as db:
        db.add(Strategy(id=1, name="终审", template_key="test"))
        db.add(StrategyRun(id=1, strategy_id=1, inst_id="BTC-USDT-SWAP",
            bar="1H", mode="ai-trade", status="paused", start_ts=1,
            config_json=json.dumps({"costBasis": "net"})))
        await db.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


def _trade(side, *, qty=1, pnl=0, after=0, event_id="leg"):
    return {"ts": 100, "side": side, "qty": qty, "price": 100,
        "fee": 0, "pnl": pnl, "positionAfter": after,
        "explain": {"meta": {"accountingEventId": event_id,
            "wallTs": 100, "feeKnown": True, "feeBasis": "measured"}}}


async def _append(factory, trade):
    async with factory() as db:
        await repositories.append_live_trade(db, 1, trade)


async def _observation_session(factory):
    session = _session()
    session.group_id = "review-cas"
    executor = session.executor
    executor._settlement_fn = AsyncMock(return_value=[])
    executor.configure_settlement_identity(IdentityConfig(
        1, 1, session.inst_id, {"tickSz": .1, "marginMode": "cross"}))
    async with factory() as db:
        await state_repo.upsert_group_state(db, state_repo.GroupStateWrite(
            session.group_id, "running", account_id=1,
            members=[session.state_member_row()]))
    executor.settlement_identity.record_trade(
        _trade("long", after=1, event_id="new-open"))
    return session


async def test_committed_observation_lost_ack_recovers_new_epoch(
        recovery_database):
    session = await _observation_session(recovery_database)
    failed = []

    def lose_ack(_database):
        if not failed:
            failed.append(True)
            raise RuntimeError("模拟提交成功但回执失败")

    event.listen(Session, "after_commit", lose_ack)
    try:
        assert not await session._persist_settlement_identity()
    finally:
        event.remove(Session, "after_commit", lose_ack)
    await session._sync_execution_integrity()
    assert not session._settlement_persist_failed
    assert not session._execution_state_persist_failed
    assert session._settlement_revision == 1
    assert session._settlement_observation == (
        session.executor.settlement_identity.observation_wire())
    async with recovery_database() as db:
        runtime = (await state_repo.load_group_state(
            db, session.group_id))["members"][0]["runtimeIntegrity"]
    assert runtime["settlementStateRevision"] == 1


async def test_unrelated_committed_epoch_cannot_be_overwritten(
        recovery_database):
    session = await _observation_session(recovery_database)
    proposed = session.executor.settlement_identity.observation_wire()
    async with recovery_database() as db:
        assert await state_repo.patch_runtime_integrity(db,
            session.group_id, session.inst_id, {
                "accountId": 1, "runId": 1, "expectedEpochKey": None,
                "expectedSettlementRevision": 0,
                "runtimeIntegrity": {"settlementContext": {
                    **proposed, "epochKey": "another-epoch"}}})
    for _ in range(2):
        assert not await session._persist_settlement_identity()
    assert session._settlement_revision == 0
    async with recovery_database() as db:
        runtime = (await state_repo.load_group_state(
            db, session.group_id))["members"][0]["runtimeIntegrity"]
    assert runtime["settlementContext"]["epochKey"] == "another-epoch"


@pytest.mark.parametrize("phase", ["adopt", "start"])
async def test_canceled_cold_resume_disposes_all_members(monkeypatch, phase):
    sessions = [SimpleNamespace(start=AsyncMock(), stop=AsyncMock(),
        on_stopped=object()) for _ in range(2)]
    group = SimpleNamespace(members=[SimpleNamespace(session=session)
                                     for session in sessions])
    assessment = SimpleNamespace(verdict="ready", req=SimpleNamespace())
    adopt = AsyncMock()
    target = adopt if phase == "adopt" else sessions[1].start
    target.side_effect = asyncio.CancelledError()
    monkeypatch.setattr(resume, "assess_group",
                        AsyncMock(return_value=assessment))
    monkeypatch.setattr(resume, "_build_resumed_group",
                        AsyncMock(return_value=group))
    monkeypatch.setattr(resume, "_adopt_group", adopt)
    with pytest.raises(asyncio.CancelledError):
        await resume.resume_cold_group(None, "review", acknowledged=True)
    for session in sessions:
        session.stop.assert_awaited_once_with("resume_failed", flatten=False)
        assert session.on_stopped is None


async def test_orphan_close_preserves_unknown_realized_and_raw_loss(
        recovery_database):
    await _append(recovery_database, _trade("close", pnl=-30))
    full = await resume.rebuild_continuity(1, net_mode=None)
    assert full["realizedPnlUsd"] is None
    assert full["tradingNetUsd"] is None
    assert full["positionSeq"] == 0
    assert full["trades"][0]["pnl"] == -30


@pytest.mark.parametrize("unknown", [False, True])
async def test_final_close_brake_uses_complete_trip_economics(
        monkeypatch, unknown):
    session = _session()
    session._sizing.brake_rule = BrakeRule(0, 1, 0, 2, 60)
    if unknown:
        session._sizing.state = replace(session._sizing.state, loss_streak=1)
    for name in ("_persist_trade", "_persist_state_position", "_push"):
        monkeypatch.setattr(session, name, AsyncMock())
    monkeypatch.setattr(session._experience, "persist_pending", AsyncMock())
    monkeypatch.setattr(session._intel_cite, "persist_pending", AsyncMock())
    opened = _trade("long", qty=2, after=2, event_id="open")
    opened["explain"]["meta"]["feeKnown"] = not unknown
    partial = _trade("close", pnl=-10, after=1, event_id="partial")
    final = _trade("close", pnl=5, event_id="final")
    if unknown:
        for trade in (partial, final):
            trade["explain"]["meta"].update(
                pnlBasis="estimated", entryFeeBasis="unknown")
    for trade in (opened, partial, final):
        await session._dispatch([{"type": "trade", "data": trade}])
    assert session._position_seq == 1
    assert session._sizing.state.loss_streak == 1
    assert final["pnl"] == 5 and partial["pnl"] == -10
    await session._dispatch([{"type": "trade", "data": final}])
    assert session._sizing.state.loss_streak == 1


def _assessment(actual_qty):
    actual = {"instId": "BTC-USDT-SWAP", "posSide": "long",
              "pos": actual_qty, "avgPx": 100} if actual_qty else None
    contract = {"instId": "BTC-USDT-SWAP", "runId": 1,
                "runtimeIntegrity": {}}
    return SimpleNamespace(creds=None,
        members=[{"instId": "BTC-USDT-SWAP", "verdict": "partially_closed",
                  "actual": actual}],
        row={"members": [contract], "createdTs": 1, "pausedTs": 120})


@pytest.mark.parametrize("actual_qty", [0, 1])
async def test_resume_blocks_quantity_gap_before_start(
        recovery_database, actual_qty):
    await _append(recovery_database,
                  _trade("long", qty=2, after=2, event_id="open"))
    member = _new_member(1)
    group = SimpleNamespace(members=[member])
    with pytest.raises(resume.AiTradeResumeBlocked) as blocked:
        await resume._adopt_group(group, _assessment(actual_qty))
    assert blocked.value.members[0]["blockedReason"] == "settlement_unresolved"
    full = await resume.rebuild_continuity(1, net_mode=None)
    assert len(full["trades"]) == 1 and full["positionSeq"] == 0


async def test_resume_allows_quantity_explained_by_persisted_trades(
        recovery_database):
    await _append(recovery_database,
                  _trade("long", qty=2, after=2, event_id="open"))
    member = _new_member(1)
    await resume._adopt_group(SimpleNamespace(members=[member]),
                              _assessment(2))
    assert member.session.executor.entry_qty_sz == 2


async def test_resume_checks_quantity_after_pending_adoption(
        recovery_database, monkeypatch):
    member = _new_member(1)

    async def resolve_pending(_member):
        await _append(recovery_database,
                      _trade("long", after=1, event_id="pending-fill"))

    monkeypatch.setattr(resume, "_restore_pending_state", resolve_pending)
    await resume._adopt_group(SimpleNamespace(members=[member]),
                              _assessment(1))
    full = await resume.rebuild_continuity(1, net_mode=None)
    assert len(full["trades"]) == 1
    assert member.session.executor.entry_qty_sz == 1
