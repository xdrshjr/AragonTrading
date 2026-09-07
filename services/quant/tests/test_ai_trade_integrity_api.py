"""一致性状态的真实 SQLite 并发写与响应契约。"""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import ai_trade_state_repositories as repo
from app.db.models import Base


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
