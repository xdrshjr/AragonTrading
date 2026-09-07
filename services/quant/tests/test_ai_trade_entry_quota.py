"""逆势额度仅在接收委托后消费，恢复不凭空补充额度。"""
import importlib.util

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def sqlite_state(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.db.models import Base, Strategy, StrategyRun
    from app.db import ai_trade_state_repositories as state_repo
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'execution.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(Strategy(id=1, name="离线", template_key="test"))
        session.add(StrategyRun(
            id=1, strategy_id=1, inst_id="BTC-USDT-SWAP",
            bar="1H", mode="ai-trade"))
        await session.commit()
        await state_repo.upsert_group_state(session, state_repo.GroupStateWrite(
            group_id="g", status="running", account_id=1,
            members=[{"instId": "BTC-USDT-SWAP", "runId": 1,
                      "unknownMemberKey": "保留"}]))
    try:
        yield sessions
    finally:
        await engine.dispose()


def _quota_type():
    assert importlib.util.find_spec("app.ai_auto.entry_quota") is not None
    from app.ai_auto.entry_quota import EntryQuota
    return EntryQuota


def test_reservation_consumption_and_release_are_idempotent():
    quota = _quota_type()(run_id="run", max_per_session=2)
    assert quota.reserve("a", "cl-a")
    assert quota.reserve("a", "cl-a")
    assert quota.used == 0 and quota.reserved == 1
    assert quota.consume("a")
    assert quota.consume("a")
    assert not quota.release("a")
    assert quota.reserve("b", "cl-b")
    assert not quota.reserve("c", "cl-c")
    assert quota.release("b")
    assert quota.reserve("c", "cl-c")
    assert quota.used + quota.reserved <= quota.max_per_session


def test_restore_preserves_pending_and_consumed_identity():
    kind = _quota_type()
    quota = kind(run_id="run", max_per_session=3)
    quota.reserve("a", "cl-a")
    quota.consume("a")
    quota.reserve("b", "cl-b")
    restored = kind.restore(quota.to_dict(), run_id="run", max_per_session=3)
    assert restored.used == 1 and restored.reserved == 1
    assert restored.remaining == 1
    assert restored.to_dict() == quota.to_dict()


@pytest.mark.parametrize("snapshot", [None, {"schemaVersion": 999}])
def test_legacy_or_unknown_schema_does_not_reset_quota(snapshot):
    quota = _quota_type().restore(
        snapshot, run_id="run", max_per_session=3, legacy_used=1)
    assert quota.remaining == 0
    assert quota.coverage == "legacy_unknown"
    assert quota.legacy_assumed_used == 3


def test_resume_keeps_original_run_capacity_when_settings_change():
    kind = _quota_type()
    quota = kind("run", 3)
    for identity in ("a", "b"):
        quota.reserve(identity, identity)
        quota.consume(identity)
    restored = kind.restore(quota.to_dict(), run_id="run", max_per_session=1)
    assert restored.max_per_session == 3
    assert restored.used + restored.reserved <= restored.max_per_session


@pytest.mark.parametrize("corrupt", [
    {"reservations": None}, {"consumedAttempts": ["bad"]},
    {"legacyAssumedUsed": "invalid"},
    {"reservations": {"a": "one"}, "consumedAttempts": {"a": "one"}},
])
def test_malformed_snapshot_cannot_grant_or_overdraw_quota(corrupt):
    kind = _quota_type()
    snapshot = kind("run", 2).to_dict() | corrupt
    restored = kind.restore(snapshot, run_id="run", max_per_session=2)
    assert restored.remaining == 0
    assert restored.coverage == "legacy_unknown"
    assert restored.used + restored.reserved <= restored.max_per_session


def test_quota_preserves_unknown_json_and_future_schema():
    kind = _quota_type()
    snapshot = kind("run", 2).to_dict() | {"futureKey": {"keep": True}}
    restored = kind.restore(snapshot, run_id="run", max_per_session=2)
    assert restored.to_dict()["futureKey"] == {"keep": True}
    future = {"schemaVersion": 999, "futureKey": {"keep": True}}
    restored = kind.restore(future, run_id="run", max_per_session=2)
    assert restored.remaining == 0
    assert restored.to_dict() == future


@pytest.mark.parametrize("lane", ["consensus", "contrarian"])
@pytest.mark.parametrize("crash_at", ["prepared", "post", "ack", "trade"])
async def test_sqlite_cold_recovery_preserves_fixed_identity_and_one_trade(
        sqlite_state, lane, crash_at):
    from sqlalchemy import select
    from app.ai_auto.open_attempts import OpenAttemptManager
    from app.db import ai_trade_state_repositories as state_repo
    from app.db import repositories as repo
    from app.db.models import Trade
    from app.okx.order_builder import OrderInput, SubmissionTrace
    from app.okx.private_client import OKXPrivateError
    from app.okx.trading import new_client_order_id
    from app.schemas import trade_out
    from tests.test_live_executor import _Sig, _make_executor
    executor, _ = _make_executor()
    posts = []

    async def persist(runtime):
        async with sqlite_state() as session:
            return await state_repo.patch_runtime_integrity(
                session, "g", executor.inst_id,
                {"runId": 1, "runtimeIntegrity": runtime})

    manager = OpenAttemptManager(
        account_id=1, run_id=1, inst_id=executor.inst_id,
        quota=_quota_type()(1, 2), persist_callback=persist)
    manager.set_context(round=7, runtime_epoch_id="old", lane=lane)
    executor.open_attempts = manager

    async def lookup(identity):
        return {"instId": executor.inst_id, "side": "buy", "sz": 10,
                "clOrdId": identity, "ordId": "okx1", "state": "filled",
                "avgPx": 29900, "accFillSz": 10}

    async def post(order):
        posts.append(order.client_order_id)
        async with sqlite_state() as session:
            stored = await state_repo.load_group_state(session, "g")
        pending = stored["members"][0]["runtimeIntegrity"]["pendingOpenAttempt"]
        assert pending["clOrdId"] == order.client_order_id
        order.submission_trace.phase = "sent"
        if crash_at == "post":
            raise OKXPrivateError("OKX_UNREACHABLE", "响应丢失")
        order.submission_trace.ack = "accepted"
        order.submission_trace.ord_id = "okx1"
        if crash_at == "ack":
            raise RuntimeError("接收后崩溃")
        return {"ordId": "okx1", "clOrdId": order.client_order_id}

    executor._place_order, executor._get_order = post, lookup
    if crash_at == "prepared":
        assert await manager.prepare(OrderInput(
            executor.inst_id, "long", "market", "cross", None, 10, 3,
            None, None, new_client_order_id(), SubmissionTrace()))
    else:
        try:
            events = await executor.apply(
                [_Sig("long")], ref_px=30000, bar_index=0, ts=1)
        except RuntimeError:
            events = []
        if crash_at == "trade":
            async with sqlite_state() as session:
                for event in events:
                    if event["type"] == "trade":
                        await repo.append_live_trade(session, 1, event["data"])
                        manager.mark_events_persisted(
                            event["data"]["explain"]["meta"]["accountingEventId"])
    original_id = manager.pending.cl_ord_id
    async with sqlite_state() as session:
        stored = await state_repo.load_group_state(session, "g")
        rows = [trade_out(row) for row in (await session.execute(
            select(Trade).order_by(Trade.id))).scalars().all()]
    cold, _ = _make_executor()
    restored = OpenAttemptManager.restore(
        stored["members"][0]["runtimeIntegrity"], account_id=1, run_id=1,
        inst_id=executor.inst_id, max_per_session=2, persist_callback=persist)
    cold.open_attempts = restored
    cold.restore_trades(rows)
    if rows:
        cold.projected, cold.entry_price, cold.entry_qty_sz = "long", 29900, 10
    cold._get_order, cold._place_order = lookup, post
    restored.set_context(round=7, runtime_epoch_id="new", lane=lane)
    for _ in range(2):
        events = await restored.resolve_pending(cold, 2)
        async with sqlite_state() as session:
            for event in events:
                if event["type"] == "trade":
                    await repo.append_live_trade(session, 1, event["data"])
                    restored.mark_events_persisted(
                        event["data"]["explain"]["meta"]["accountingEventId"])
    assert restored.pending.cl_ord_id == original_id
    assert await restored.complete_adoption(cold)
    assert len(posts) == (0 if crash_at == "prepared" else 1)
    assert restored.quota.used == (1 if lane == "contrarian" else 0)
    async with sqlite_state() as session:
        rows = (await session.execute(select(Trade))).scalars().all()
        stored = await state_repo.load_group_state(session, "g")
    assert len(rows) == 1 and rows[0].price == 29900 and rows[0].qty == 10
    runtime = stored["members"][0]["runtimeIntegrity"]
    assert runtime["pendingOpenAttempt"] is None
    assert runtime["adoptedOrder"]["clOrdId"] == original_id
    assert stored["members"][0]["unknownMemberKey"] == "保留"


async def test_sqlite_partial_resume_prices_only_the_new_fill(sqlite_state):
    import json
    from sqlalchemy import select
    from app.ai_auto.open_attempts import OpenAttemptManager
    from app.db import ai_trade_state_repositories as state_repo
    from app.db import repositories as repo
    from app.db.models import Trade
    from app.okx.order_builder import OrderInput, SubmissionTrace
    from app.schemas import trade_out
    from tests.test_live_executor import _make_executor
    executor, _ = _make_executor()

    async def persist(runtime):
        async with sqlite_state() as session:
            return await state_repo.patch_runtime_integrity(
                session, "g", executor.inst_id,
                {"runId": 1, "runtimeIntegrity": runtime})

    manager = OpenAttemptManager(
        account_id=1, run_id=1, inst_id=executor.inst_id,
        quota=_quota_type()(1, 2), persist_callback=persist)
    assert await manager.prepare(OrderInput(
        executor.inst_id, "long", "limit", "cross", 100, 10, 3,
        None, None, "fixed", SubmissionTrace()))
    await manager.observe(SubmissionTrace("sent", "accepted", "okx1"))
    async with sqlite_state() as session:
        await repo.append_live_trade(session, 1, {
            "ts": 1, "side": "long", "qty": 4, "price": 100,
            "clOrdId": "fixed", "ordId": "okx1"})
        rows = [{**trade_out(row), "explain": json.loads(row.explain_json)}
                for row in (await session.execute(
            select(Trade).order_by(Trade.id))).scalars().all()]
        stored = await state_repo.load_group_state(session, "g")
    cold, _ = _make_executor()
    cold.open_attempts = OpenAttemptManager.restore(
        stored["members"][0]["runtimeIntegrity"], account_id=1, run_id=1,
        inst_id=executor.inst_id, max_per_session=2, persist_callback=persist)
    cold.restore_trades(rows)
    cold.projected, cold.entry_price, cold.entry_qty_sz = "long", 106, 10

    async def lookup(identity):
        return {"instId": executor.inst_id, "side": "buy", "sz": 10,
                "clOrdId": identity, "ordId": "okx1", "state": "filled",
                "avgPx": 106, "accFillSz": 10}

    cold._get_order = lookup
    events = await cold.open_attempts.resolve_pending(cold, 3)
    assert events[0]["data"]["qty"] == 6
    assert events[0]["data"]["price"] == 110
    assert cold.entry_price == 106
