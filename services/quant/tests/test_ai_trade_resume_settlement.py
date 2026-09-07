"""结算恢复的真实SQLite回归：结构边界、原始来源和持久化幂等。"""
from __future__ import annotations

from types import MethodType, SimpleNamespace

import pytest
from sqlalchemy import event

from app.ai_auto.resume import (
    AiTradeResumeBlocked, _adopt_group, rebuild_continuity,
    restore_member_continuity, settle_closed_offline,
)
from app.config import Settings
from app.db import ai_trade_state_repositories as state_repo
from app.db import repositories as repo
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.live_auto.settlement_identity import IdentityConfig

_START_TS = 1_700_000_000


async def _seed_trades(run_id, rows):
    async with get_sessionmaker()() as db:
        for row in rows:
            await repo.append_live_trade(db, run_id, row)


def _new_member(run_id):
    from app.ai_auto.cost_runtime import CostRuntime
    from app.engine.simulator import RiskParams
    from app.live_auto.executor import LiveExecutor
    from app.okx.accounts import AccountCredentials

    async def empty(*_args, **_kwargs):
        return []

    settings = Settings()
    executor = LiveExecutor(
        creds=AccountCredentials("ak", "secret-fake", "pass-fake"),
        account_id=1, inst_id="BTC-USDT-SWAP", td_mode="cross",
        pos_mode="long_short_mode", leverage=3, budget_usd=100,
        risk=RiskParams(), ct_val=.01, lot_sz=.01, min_sz=.01,
        settings=settings, place_order_fn=empty, close_position_fn=empty,
        positions_fn=empty, fills_fn=empty, settlement_fn=empty)
    executor.configure_settlement_identity(IdentityConfig(1, run_id,
        executor.inst_id, {"tickSz": .1, "marginMode": "cross"}))
    session = SimpleNamespace(executor=executor, run_id=run_id,
        inst_id=executor.inst_id, _position_seq=0, started_ts=_START_TS,
        _unpersisted_trades={},
        _cost=CostRuntime(executor.inst_id, settings, None),
        decider=SimpleNamespace(seed_from_snapshot=lambda **_kwargs: None))
    return SimpleNamespace(run_id=run_id, inst_id=executor.inst_id,
                           session=session)


@pytest.fixture
async def recovery():
    await init_db()
    try:
        async with get_sessionmaker()() as db:
            run = await repo.create_run(db, strategy_id=1,
                inst_id="BTC-USDT-SWAP", bar="1H", mode="ai-trade",
                config={"costBasis": "net"}, status="paused")
        meta = {"accountingEventId": "open-1", "wallTs": _START_TS + 10,
                "feeKnown": True, "feeBasis": "estimated",
                "feeCurrency": "USDT"}
        await _seed_trades(run.id, [{"ts": _START_TS, "side": "long",
            "price": 100, "qty": 2, "fee": .5, "pnl": 0,
            "positionAfter": 2, "explain": {"meta": meta}}])
        member = _new_member(run.id)
        full = await rebuild_continuity(run.id, net_mode=None)
        tracker = member.session.executor.settlement_identity
        tracker.restore(full["trades"])
        tracker.observe_position({"instId": member.inst_id, "posId": "POS-A",
            "pos": 2, "avgPx": 100, "posSide": "long", "marginMode": "cross",
            "exchangeCTimeMs": str(_START_TS * 1000),
            "exchangeUTimeMs": str((_START_TS + 20) * 1000),
            "observedAtTs": _START_TS + 21})
        contract = {"instId": member.inst_id, "runId": run.id,
            "position": {"posId": "POS-A", "side": "long", "size": 2,
                "avgEntry": 100, "openCTime": _START_TS},
            "runtimeIntegrity": {
                "settlementContext": tracker.observation_wire(),
                "settlementStateRevision": 7, "futureKey": {"preserved": True}}}
        async with get_sessionmaker()() as db:
            await state_repo.upsert_group_state(db, state_repo.GroupStateWrite(
                group_id=f"settlement-{run.id}", status="paused", account_id=1,
                paused_ts=_START_TS + 30, members=[contract]))
        yield member, contract
    finally:
        await dispose_engine()


def _history_row():
    return {"instId": "BTC-USDT-SWAP", "instType": "SWAP", "posId": "POS-A",
        "direction": "long", "posSide": "long", "marginMode": "cross",
        "exchangeCTimeMs": str(_START_TS * 1000),
        "exchangeUTimeMs": str((_START_TS + 60) * 1000),
        "currency": "USDT", "closed": True, "type": "2", "pnl": 10,
        "fee": .2, "fundingFee": 0, "liqPenalty": 0, "settledPnl": 0,
        "realizedPnl": 9.8, "closeTotalPos": 2, "openAvgPx": 100,
        "closeAvgPx": 110}


async def _reload_contract(member):
    async with get_sessionmaker()() as db:
        state = await state_repo.load_group_state(
            db, f"settlement-{member.run_id}")
    assert state["status"] == "paused"
    assert state["resumedCount"] == 0
    return state["members"][0]


async def test_offline_gap_blocks_until_verified_then_restores_once(
        recovery, monkeypatch):
    member, contract = recovery
    rows, calls = [], []

    async def history(*_args, **kwargs):
        calls.append(kwargs)
        return rows

    monkeypatch.setattr("app.ai_auto.resume.fetch_position_history", history)
    with pytest.raises(AiTradeResumeBlocked) as blocked:
        await settle_closed_offline(member, contract, creds=None,
            cost_basis="net", paused_ts=_START_TS + 30)
    assert blocked.value.members[0]["blockedReason"] == "settlement_unresolved"
    full = await rebuild_continuity(member.run_id, net_mode=None)
    assert len(full["trades"]) == 1 and full["positionSeq"] == 0
    contract = await _reload_contract(member)
    rows.append(_history_row())
    group = SimpleNamespace(members=[member])
    assessment = SimpleNamespace(creds=None,
        members=[{"instId": member.inst_id, "verdict": "closed_offline"}],
        row={"members": [contract], "createdTs": _START_TS,
             "pausedTs": _START_TS + 30})
    await _adopt_group(group, assessment)
    assert member.session._position_seq == 1
    assert member.session.executor.entry_fee_usd == 0
    assert member.session.executor.realized_pnl_usd == pytest.approx(9.8)
    assert member.session.executor.projected == "flat"
    assert member.session.executor.settlement_identity.context() is None
    assert len(member.session.executor.settlement_identity.claimed) == 1
    await dispose_engine()
    await init_db()
    recovered = _new_member(member.run_id)
    contract = await _reload_contract(member)
    await settle_closed_offline(recovered, contract, creds=None,
        cost_basis="net", paused_ts=_START_TS + 30)
    full = await rebuild_continuity(member.run_id, net_mode=None)
    assert len(full["trades"]) == 2
    assert full["trades"][-1]["fee"] == pytest.approx(-.3)
    assert len(calls) == 2
    assert calls[0]["after_ms"] == (_START_TS - 30) * 1000 - 1
    assert contract["runtimeIntegrity"]["futureKey"] == {"preserved": True}


async def test_restore_current_epoch_retains_observation_revision_and_wall(
        recovery):
    member, contract = recovery
    full = await rebuild_continuity(member.run_id, net_mode=None)
    restore_member_continuity(member, full, contract)
    assert member.session._settlement_revision == 7
    assert member.session.executor._entry_wall_ts() == _START_TS + 10
    assert member.session.executor.entry_fee_usd == .5
    assert member.session._cost.remaining_open_fee_usd == .5
    assert member.session._settlement_observation == contract[
        "runtimeIntegrity"]["settlementContext"]


async def test_recovered_projected_net_retains_unknown_coverage(recovery):
    member, _contract = recovery
    await _seed_trades(member.run_id, [{"ts": _START_TS + 60,
        "side": "close", "qty": 2, "price": 110, "fee": .2, "pnl": 9.8,
        "positionAfter": 0, "explain": {"meta": {
            "feeKnown": True, "feeBasis": "estimated",
            "pnlBasis": "estimated", "entryFeeBasis": "unknown"}}}])
    full = await rebuild_continuity(member.run_id, net_mode=None)
    assert full["realizedPnlUsd"] is None
    assert full["tradingNetUsd"] is None
    assert full["positionSeq"] == 1
    assert full["trades"][-1]["pnl"] == 9.8


async def test_failed_resume_disposes_only_local_resources(recovery):
    """真实stop分支不能对未启动实例结算、归档或写收益快照。"""
    from app.ai_auto.session import AiTradeSession
    from app.db.models import StrategyRun

    member, _contract = recovery
    session, closed = member.session, []

    async def stop_ws():
        closed.append("ws")

    def forbid_accounting():
        pytest.fail("恢复失败清理进入了交易或会计终结路径")

    session.is_active = True
    session.state = "running"
    session._cancel_task = lambda name: closed.append(name)
    session._backstop = SimpleNamespace(close=lambda: closed.append("backstop"))
    session._repair = SimpleNamespace(close=lambda: closed.append("repair"))
    session._ws = SimpleNamespace(stop=stop_ws)
    session._ref_px = session._ref_ts = forbid_accounting
    session._dispose_failed_resume = MethodType(
        AiTradeSession._dispose_failed_resume, session)
    result = await AiTradeSession.stop(session, "resume_failed", flatten=False)
    assert result["ok"] is True
    assert set(closed) == {"_monitor_task", "_decision_task", "backstop",
                           "repair", "ws"}
    async with get_sessionmaker()() as db:
        run = await db.get(StrategyRun, member.run_id)
    assert run.status == "paused" and run.end_ts is None
    assert len((await rebuild_continuity(member.run_id, net_mode=None))[
        "trades"]) == 1


async def test_committed_close_recovers_hot_state_without_second_trade(
        recovery, monkeypatch):
    from app.ai_auto.session import AiTradeSession

    member, contract = recovery
    full = await rebuild_continuity(member.run_id, net_mode=None)
    restore_member_continuity(member, full, contract)
    context = member.session.executor.settlement_identity.context()

    async def history(*_args, **_kwargs):
        return [_history_row()]

    monkeypatch.setattr("app.ai_auto.resume.fetch_position_history", history)
    await settle_closed_offline(member, contract, creds=None,
        cost_basis="net", paused_ts=_START_TS + 30)
    session = _new_member(member.run_id).session
    session.state_member_row = lambda: contract
    pending = SimpleNamespace(context=context, validation=None)
    assert await AiTradeSession._restore_claimed_settlement(session, pending)
    assert session._position_seq == 1
    assert session.executor.realized_pnl_usd == pytest.approx(9.8)
    assert session.executor.entry_qty_sz == 0
    assert session.executor.entry_fee_usd == 0
    assert session.executor.projected == "flat"
    full = await rebuild_continuity(member.run_id, net_mode=None)
    assert len(full["trades"]) == 2


@pytest.mark.parametrize("phase,stored", [("before_insert", 1),
                                         ("after_commit", 2)])
async def test_sqlite_failure_retry_keeps_one_close(recovery, monkeypatch,
                                                   phase, stored):
    """在真实数据库执行边界注入故障，不替换交易持久化仓库。"""
    member, contract = recovery

    async def history(*_args, **_kwargs):
        return [_history_row()]

    def reject_write(_conn, _cursor, statement, *_args):
        prefix = ("INSERT INTO TRADES" if phase == "before_insert"
                  else "UPDATE STRATEGY_RUNS")
        if statement.lstrip().upper().startswith(prefix):
            raise RuntimeError("数据库边界故障注入")

    monkeypatch.setattr("app.ai_auto.resume.fetch_position_history", history)
    async with get_sessionmaker()() as db:
        await repo.finalize_run(db, member.run_id, status="paused", metrics={
            "aiStats": {"offlineGap": {"pnlUnknown": True}}})
        engine = db.bind.sync_engine
    event.listen(engine, "before_cursor_execute", reject_write)
    try:
        with pytest.raises(RuntimeError, match="数据库边界故障注入"):
            await settle_closed_offline(member, contract, creds=None,
                cost_basis="net", paused_ts=_START_TS + 30)
    finally:
        event.remove(engine, "before_cursor_execute", reject_write)
    full = await rebuild_continuity(member.run_id, net_mode=None)
    assert len(full["trades"]) == stored
    recovered = _new_member(member.run_id)
    await settle_closed_offline(recovered, contract, creds=None,
        cost_basis="net", paused_ts=_START_TS + 30)
    full = await rebuild_continuity(member.run_id, net_mode=None)
    assert len(full["trades"]) == 2
    assert full["positionSeq"] == 1
    assert full["realizedPnlUsd"] == pytest.approx(9.8)
    from app.ai_auto.resume import _run_snapshot
    assert "offlineGap" not in (await _run_snapshot(member.run_id))["aiStats"]
    assert (await _reload_contract(member))["runtimeIntegrity"] == contract[
        "runtimeIntegrity"]


@pytest.mark.parametrize("change,accepted", [({}, True),
    ({"openWallTs": None}, False), ({"positionSeq": 1}, False),
    ({"size": 3}, False)])
async def test_legacy_offline_alias_requires_complete_paused_epoch(
        recovery, monkeypatch, change, accepted):
    """旧事件只证明同run已闭合epoch，不能伪造v1结算身份或认证金额。"""
    member, contract = recovery
    contract["runtimeIntegrity"]["settlementContext"] = None
    contract["position"].update(positionSeq=0, openWallTs=_START_TS + 10)
    contract["position"].update(change)
    await _seed_trades(member.run_id, [{"ts": _START_TS + 60,
        "side": "close", "price": 110, "qty": 2, "pnl": 9.8,
        "fee": -.3, "positionAfter": 0, "explain": {"meta": {
            "accountingEventId": f"offline:POS-A:{_START_TS + 60}",
            "posId": "POS-A", "wallTs": _START_TS + 60,
            "feeBasis": "estimated", "feeKnown": True,
            "feeCurrency": "USDT"}}}])

    async def missing_history(*_args, **_kwargs):
        return []

    monkeypatch.setattr("app.ai_auto.resume.fetch_position_history",
                        missing_history)
    if accepted:
        await settle_closed_offline(member, contract, creds=None,
            cost_basis="net", paused_ts=_START_TS + 30)
    else:
        with pytest.raises(AiTradeResumeBlocked):
            await settle_closed_offline(member, contract, creds=None,
                cost_basis="net", paused_ts=_START_TS + 30)
    full = await rebuild_continuity(member.run_id, net_mode=None)
    assert len(full["trades"]) == 2
    assert "settlementKey" not in full["trades"][-1]["explain"]["meta"]


async def test_rebuild_partial_and_unknown_fee_close_keep_structural_epochs():
    """部分平仓不推进seq，未知费用的真实全平推进；费池按数量消耗。"""
    from app.ai_auto.attribution import AttributionLedger

    await init_db()
    try:
        async with get_sessionmaker()() as db:
            run = await repo.create_run(db, strategy_id=1,
                inst_id="BTC-USDT-SWAP", bar="1H", mode="ai-trade",
                config={"costBasis": "gross"}, status="paused")
        meta = {"feeKnown": True, "feeBasis": "estimated",
                "feeCurrency": "USDT", "wallTs": _START_TS + 20}
        first = {"ts": _START_TS, "side": "long", "price": 100,
                 "qty": 2, "fee": .6, "pnl": 0, "positionAfter": 2,
                 "explain": {"meta": meta}}
        partial = {"ts": _START_TS, "side": "close", "price": 105,
                   "qty": 1, "fee": .2, "pnl": 5, "positionAfter": 1,
                   "explain": {"meta": meta}}
        await _seed_trades(run.id, [first, partial])
        rebuilt = await rebuild_continuity(run.id, net_mode=None)
        assert rebuilt["positionSeq"] == 0
        assert rebuilt["entryFeeUsd"] == pytest.approx(.3)
        ledger = AttributionLedger(net_mode=False, ct_val=.01)
        ledger.restore_accounting(rebuilt["trades"], {"costBasis": "gross"})
        ledger.note_open(dict(first, id=999999, runId=run.id, qty=1), seq=0)
        assert ledger.accounting.remaining_qty == 2
        final = {**partial, "explain": {"meta": {"feeKnown": False}},
                 "positionAfter": 0}
        await _seed_trades(run.id, [final, dict(first, fee=-.1)])
        rebuilt = await rebuild_continuity(run.id, net_mode=None)
        assert rebuilt["positionSeq"] == 1
        assert rebuilt["entryFeeUsd"] == pytest.approx(-.1)
        ledger.restore_accounting(rebuilt["trades"], {"costBasis": "gross"})
        assert ledger.accounting.current_epoch_id == 1
    finally:
        await dispose_engine()
