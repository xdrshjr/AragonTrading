"""终审：可选学习失败与撤单期间成交不能抹掉执行事实。"""
import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.ai_auto.decider import TradeDecision
from app.db.models import Base, Strategy, StrategyRun, Trade
from tests.test_ai_trade_funnel_session import _session


@pytest.fixture
async def trade_database(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'review-trades.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database:
        database.add(Strategy(id=1, name="终审", template_key="test"))
        database.add(StrategyRun(id=1, strategy_id=1, inst_id="BTC-USDT-SWAP",
                                bar="1H", mode="ai-trade", status="running",
                                start_ts=1, config_json="{}"))
        await database.commit()
    monkeypatch.setattr("app.ai_auto.session.get_sessionmaker", lambda: factory)
    try:
        yield factory
    finally:
        await engine.dispose()


def trade_event(side, event_id):
    return {"type": "trade", "data": {
        "ts": 100, "side": side, "qty": 1, "price": 100,
        "fee": 0.1, "pnl": 0, "positionAfter": 0 if side == "close" else 1,
        "explain": {"meta": {"accountingEventId": event_id}}}}


async def test_learning_failure_preserves_all_raw_trades(
        trade_database, monkeypatch):
    session = _session()
    push = AsyncMock()
    monkeypatch.setattr(session, "_push", push)

    def unavailable(*args):
        raise RuntimeError("模拟可选学习失败")

    monkeypatch.setattr(session._experience, "note_close", unavailable)
    events = [trade_event("close", "closed"), trade_event("long", "opened")]
    await session._dispatch(events)
    async with trade_database() as database:
        rows = (await database.execute(select(Trade))).scalars().all()
    assert [row.side for row in rows] == ["close", "long"]
    assert [call.args[0] for call in push.call_args_list] == events
    assert not session._trade_persist_failed


@pytest.mark.parametrize("side", ["long", "close"])
async def test_cancel_receipt_reports_concurrent_fill(monkeypatch, side):
    session = _session()
    session.state = "running"
    events = [trade_event(side, "cancel-fill")]
    monkeypatch.setattr(session.executor, "cancel_resting",
                        AsyncMock(return_value=events))
    monkeypatch.setattr(session, "_dispatch", AsyncMock())
    result = await session._execute_cancel(
        TradeDecision(action="cancel_order", reason="撤销剩余委托",
                      size_fraction=None, confidence=1), 100)
    assert result["traded"] is True
    assert result["closedFilled"] is (side == "close")


def stopping_session(monkeypatch):
    session = _session()
    session.state = "running"
    session.install_integrity()
    session.executor.open_attempts.persist_callback = AsyncMock(
        return_value=True)
    monkeypatch.setattr(session.executor, "flatten", AsyncMock(return_value={
        "ok": True, "closedSz": 1,
        "trade": trade_event("close", "stop-close")["data"]}))
    monkeypatch.setattr(session._cost, "reconcile_tick", AsyncMock())
    monkeypatch.setattr(session._intel_cite, "persist_pending", AsyncMock())
    monkeypatch.setattr(session, "_push", AsyncMock())
    session.on_stopped = AsyncMock()
    return session


async def test_stop_retries_transient_trade_insert(trade_database, monkeypatch):
    from app.db import repositories

    session = stopping_session(monkeypatch)
    append = repositories.append_live_trade
    calls = []

    async def transient(*args):
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("模拟一次成交写入失败")
        return await append(*args)

    monkeypatch.setattr(repositories, "append_live_trade", transient)
    result = await session.stop()
    async with trade_database() as database:
        rows = (await database.execute(select(Trade))).scalars().all()
    assert result["ok"] is True
    assert len(rows) == 1
    assert not session._trade_persist_failed
    assert session.executor.flatten.await_count == 1


async def test_repeated_stop_flushes_backlog_without_another_order(
        trade_database, monkeypatch):
    from app.db import repositories

    session = stopping_session(monkeypatch)
    append = repositories.append_live_trade
    monkeypatch.setattr(repositories, "append_live_trade",
                        AsyncMock(side_effect=RuntimeError("暂时不可写")))
    failed = await session.stop()
    assert failed["ok"] is False
    assert failed["tradePersistenceFailed"] is True
    saved = session.executor.open_attempts.runtime_payload()["pendingTrades"]
    assert saved[0]["explain"]["meta"]["accountingEventId"] == "stop-close"
    async with trade_database() as database:
        assert (await database.get(StrategyRun, 1)).status == "running"
    monkeypatch.setattr(repositories, "append_live_trade", append)
    persistence = session.executor.open_attempts.persist_callback
    persistence.return_value = False
    assert (await session.stop())["ok"] is False
    persistence.return_value = True
    assert (await session.stop())["ok"] is True
    assert (await session.stop())["ok"] is True
    async with trade_database() as database:
        assert len((await database.execute(select(Trade))).scalars().all()) == 1
        assert (await database.get(StrategyRun, 1)).status == "stopped"
    assert session.executor.flatten.await_count == 1
    assert session.executor.open_attempts.runtime_payload()["pendingTrades"] == []


async def test_canceled_trade_insert_remains_retryable(trade_database, monkeypatch):
    from app.db import repositories

    session = stopping_session(monkeypatch)
    append = repositories.append_live_trade
    started = asyncio.Event()

    async def wait_for_cancel(*args):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(repositories, "append_live_trade", wait_for_cancel)
    pending = asyncio.create_task(session._dispatch([
        trade_event("long", "cancelled-insert")]))
    await asyncio.wait_for(started.wait(), timeout=5)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert session._trade_persist_failed
    assert "cancelled-insert" in session._unpersisted_trades
    monkeypatch.setattr(repositories, "append_live_trade", append)
    assert await session._flush_stopping_trades()
    async with trade_database() as database:
        assert len((await database.execute(select(Trade))).scalars().all()) == 1
