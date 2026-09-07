"""数据库与恢复契约同时不可写时，管理器必须保留内存重试入口。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ai_auto.manager import AiTradeManager, AiTradePreflightError
from app.ai_auto.manager import AiTradeSessionLimitError
from app.config import Settings


def _pending_group(kind):
    session = SimpleNamespace(
        session_id="s", run_id=1, inst_id="BTC-USDT-SWAP",
        state="stopped", is_active=False, _unpersisted_trades={},
        _trade_persist_failed=False, _trade_backlog_persist_failed=False,
        flatten_result={"ok": False})
    if kind == "queue":
        session._unpersisted_trades = {"event": {"side": "close"}}
    elif kind == "receipt":
        session.flatten_result["tradePersistenceFailed"] = True
    else:
        setattr(session, kind, True)
    member = SimpleNamespace(inst_id=session.inst_id, session=session, run_id=1)
    group = SimpleNamespace(group_id="g", members=[member], state="running")
    manager = AiTradeManager()
    manager._group = group
    manager._unwire_intel = lambda group_id: None
    return manager, group, session


_KINDS = ["queue", "receipt", "_trade_persist_failed",
          "_trade_backlog_persist_failed"]


@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("scope", ["group", "member"])
async def test_failed_stop_keeps_same_session_until_persistence_retry(kind, scope):
    manager, group, session = _pending_group(kind)
    manager._stop_session = AsyncMock(side_effect=lambda *args: session.flatten_result)
    stop = manager.stop_group if scope == "group" else manager.stop_member
    args = ("g",) if scope == "group" else ("g", session.inst_id)
    assert await stop(*args) is not None
    assert manager._group is group
    session._unpersisted_trades.clear()
    session._trade_persist_failed = session._trade_backlog_persist_failed = False
    session.flatten_result = {"ok": True}
    assert await stop(*args) is not None
    assert manager._group is None
    assert manager._stop_session.await_count == 2


@pytest.mark.parametrize("kind", _KINDS)
async def test_unpersisted_session_blocks_start_before_database_probe(kind, monkeypatch):
    from app.ai_auto import manager as manager_module

    manager, _, _ = _pending_group(kind)

    def unavailable_database():
        raise AssertionError("内存待办必须先于数据库探测阻断")

    monkeypatch.setattr(manager_module, "get_sessionmaker", unavailable_database)
    with pytest.raises(AiTradeSessionLimitError):
        await manager._require_capacity(Settings(ai_trade_pause_enabled=False))


@pytest.mark.parametrize("kind", _KINDS)
async def test_resume_cannot_clear_or_restart_stopped_session_with_pending_trades(kind):
    manager, group, _ = _pending_group(kind)
    manager._lifecycle_truth_enabled = lambda group: False
    with pytest.raises(AiTradePreflightError):
        await manager._preflight_resume(group)
    assert manager._group is group
    manager._clear_group_if_current(group)
    assert manager._group is group


@pytest.mark.parametrize("kind", ["queue", "_trade_backlog_persist_failed"])
async def test_end_cannot_discard_inactive_member_with_unpersisted_trade(kind):
    manager, group, _ = _pending_group(kind)
    manager._load_state_row = AsyncMock(return_value=None)
    manager._mark_state_ended = AsyncMock()
    with pytest.raises(AiTradePreflightError):
        await manager.end_group("g", flatten=False, acknowledged=True)
    assert manager._group is group
    manager._mark_state_ended.assert_not_awaited()


async def test_end_retains_member_when_stop_produces_unpersisted_trade():
    manager, group, session = _pending_group("queue")
    session._unpersisted_trades.clear()
    session.is_active = True
    manager._load_state_row = AsyncMock(return_value=None)
    manager._mark_state_ended = AsyncMock()

    async def fail_persistence(*args, **kwargs):
        session.is_active = False
        session._unpersisted_trades["close"] = {"side": "close"}
        return {"ok": False, "tradePersistenceFailed": True}

    manager._stop_session = fail_persistence
    with pytest.raises(AiTradePreflightError):
        await manager.end_group("g", flatten=False, acknowledged=True)
    assert manager._group is group
    manager._mark_state_ended.assert_not_awaited()
