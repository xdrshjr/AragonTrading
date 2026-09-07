"""停止期间未落库的原始成交必须可持久恢复，重放不发交易请求。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.ai_auto import resume
from app.db import ai_trade_state_repositories as state_repo
from app.db import repositories as repo
from app.db.models import Trade
from tests import test_ai_trade_entry_quota as quota_tests
from tests.test_ai_trade_execution_receipts import _manager
from tests.test_live_executor import _make_executor


sqlite_state = quota_tests.sqlite_state


def _trade(event_id="stopped-close"):
    return {
        "runId": 1, "ts": 2, "side": "close", "price": 110,
        "qty": 1, "pnl": 10, "fee": 0, "positionAfter": 0,
        "explain": {"meta": {"runId": 1,
                              "accountingEventId": event_id}},
    }


async def _save_queue(sessions, queue):
    async with sessions() as db:
        assert await state_repo.patch_runtime_integrity(
            db, "g", "BTC-USDT-SWAP", {
                "runId": 1, "position": None, "positionUnknown": False,
                "runtimeIntegrity": {
                    "entryQuota": {"preserved": True}, "pendingTrades": queue,
                },
            })
        await state_repo.patch_group_state(db, "g", {"status": "ended"})


@pytest.mark.parametrize("queue", [[_trade()], "unreadable", {}])
async def test_pending_raw_trades_preserve_ended_members(sqlite_state, queue):
    await _save_queue(sqlite_state, queue)
    async with sqlite_state() as db:
        assert await state_repo.load_pending_groups(db)
        await state_repo.remove_member_state(db, "g", "BTC-USDT-SWAP", 1)
        assert await state_repo.delete_group_state(db, "g") == 0
        stored = await state_repo.load_group_state(db, "g")
    assert stored["members"][0]["runtimeIntegrity"]["pendingTrades"] == queue


async def test_replay_committed_raw_trade_once_preserves_saved_flat_position(
        sqlite_state):
    trade = _trade()
    await _save_queue(sqlite_state, [trade])
    async with sqlite_state() as db:
        await repo.append_live_trade(db, 1, trade)
        assert await state_repo.retry_pending_trades(db, "g")
        assert await state_repo.retry_pending_trades(db, "g")
        trades = (await db.execute(select(Trade))).scalars().all()
        stored = await state_repo.load_group_state(db, "g")
    assert len(trades) == 1
    member = stored["members"][0]
    assert member["position"] is None
    assert member["positionUnknown"] is False
    assert member["runtimeIntegrity"]["pendingTrades"] == []
    assert member["runtimeIntegrity"]["entryQuota"] == {"preserved": True}


async def test_replaying_older_close_preserves_newer_position_snapshot(
        sqlite_state):
    await _save_queue(sqlite_state, [_trade()])
    newer_position = {"side": "short", "size": 2, "positionSeq": 3}
    async with sqlite_state() as db:
        await state_repo.patch_member_state(db, "g", "BTC-USDT-SWAP", {
            "runId": 1, "position": newer_position})
        assert await state_repo.retry_pending_trades(db, "g")
        stored = await state_repo.load_group_state(db, "g")
    assert stored["members"][0]["position"] == newer_position


async def test_replay_failure_preserves_queue_and_retry_does_not_duplicate(
        sqlite_state, monkeypatch):
    queue = [_trade("first"), _trade("second")]
    await _save_queue(sqlite_state, queue)
    append = repo.append_live_trade

    async def fail_second(db, run_id, trade):
        if trade["explain"]["meta"]["accountingEventId"] == "second":
            raise RuntimeError("成交写入暂不可用")
        await append(db, run_id, trade)

    monkeypatch.setattr(repo, "append_live_trade", fail_second)
    async with sqlite_state() as db:
        with pytest.raises(RuntimeError, match="成交写入暂不可用"):
            await state_repo.retry_pending_trades(db, "g")
        stored = await state_repo.load_group_state(db, "g")
    assert stored["members"][0]["runtimeIntegrity"]["pendingTrades"] == queue
    monkeypatch.setattr(repo, "append_live_trade", append)
    async with sqlite_state() as db:
        assert await state_repo.retry_pending_trades(db, "g")
        trades = (await db.execute(select(Trade))).scalars().all()
    assert len(trades) == 2


@pytest.mark.parametrize("queue", ["broken", {}, [{"runId": 1}],
                                        [_trade() | {"runId": 2}]])
async def test_unreadable_pending_trades_never_silently_disappear(
        sqlite_state, queue):
    await _save_queue(sqlite_state, queue)
    async with sqlite_state() as db:
        with pytest.raises(ValueError):
            await state_repo.retry_pending_trades(db, "g")
        stored = await state_repo.load_group_state(db, "g")
        assert not (await db.execute(select(Trade))).scalars().all()
    assert stored["members"][0]["runtimeIntegrity"]["pendingTrades"] == queue


async def test_resume_replays_raw_close_before_any_exchange_preflight(
        sqlite_state, monkeypatch):
    await _save_queue(sqlite_state, [_trade()])
    monkeypatch.setattr(resume, "get_sessionmaker", lambda: sqlite_state)

    async def stop_after_replay(row, settings):
        async with sqlite_state() as db:
            assert len((await db.execute(select(Trade))).scalars().all()) == 1
        assert row["members"][0]["position"] is None
        raise RuntimeError("已验证先补写成交，再进入外部预检")

    monkeypatch.setattr(resume, "_rebuild_request", stop_after_replay)
    with pytest.raises(RuntimeError, match="已验证先补写成交"):
        await resume.assess_group(SimpleNamespace(), "g")


async def test_durable_trade_queue_blocks_new_open_and_is_copied():
    from app.okx.order_builder import OrderInput, SubmissionTrace

    executor, _ = _make_executor()
    manager = _manager(executor, None)
    queue = [_trade()]
    manager.set_pending_trades(queue)
    assert manager.has_pending
    queue[0]["pnl"] = -999
    expected = deepcopy(_trade())
    assert manager.runtime_payload()["pendingTrades"] == [expected]
    order = OrderInput(executor.inst_id, "long", "market", "cross",
                       None, 1, 3, None, None, "new", SubmissionTrace())
    assert not await manager.prepare(order)
    assert manager.block_reason == "attempt_persist_failed"
