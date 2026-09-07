"""不可读的恢复契约也必须占住成员和分组恢复入口。"""
from types import SimpleNamespace

import pytest

from app.ai_auto.open_attempts import OpenAttemptManager
from app.db import ai_trade_state_repositories as state_repo
from tests import test_ai_trade_entry_quota as quota_tests
from tests.test_live_executor import _make_executor
from tests.test_ai_trade_execution_integrity_review import _partial_open


sqlite_state = quota_tests.sqlite_state


@pytest.mark.parametrize("pending", [{}, "", False, []])
@pytest.mark.parametrize("operation", ["lookup", "remove", "delete", "snapshot"])
async def test_unreadable_pending_preserves_sqlite_recovery_contract(
        sqlite_state, pending, operation):
    async with sqlite_state() as session:
        assert await state_repo.patch_runtime_integrity(
            session, "g", "BTC-USDT-SWAP", {
                "runId": 1,
                "runtimeIntegrity": {"pendingOpenAttempt": pending},
            })
        if operation == "lookup":
            assert await state_repo.load_pending_groups(session)
        elif operation == "remove":
            await state_repo.remove_member_state(
                session, "g", "BTC-USDT-SWAP", 1)
        elif operation == "delete":
            assert await state_repo.delete_group_state(session, "g") == 0
        else:
            await state_repo.patch_group_state(session, "g", {"members": []})
        stored = await state_repo.load_group_state(session, "g")
    assert stored is not None
    assert len(stored["members"]) == 1
    assert stored["members"][0]["runtimeIntegrity"]["pendingOpenAttempt"] == pending


@pytest.mark.parametrize("adopted", ["", [], {"resting": {}}, {"resting": []}])
def test_unreadable_adopted_order_blocks_restart_and_retains_original(adopted):
    executor, _ = _make_executor()
    restored = OpenAttemptManager.restore(
        {"adoptedOrder": adopted}, account_id=1, run_id="run",
        inst_id=executor.inst_id, max_per_session=2)
    assert not restored.restore_resting(executor)
    assert restored.has_pending
    assert restored.runtime_payload()["adoptedOrder"] == adopted
    assert state_repo._has_pending({"runtimeIntegrity": {"adoptedOrder": adopted}})


@pytest.mark.parametrize("runtime", [
    {"pendingOpenAttempt": {}}, {"adoptedOrder": []},
    {"adoptedOrder": {"resting": {}}},
])
async def test_resume_preview_blocks_unreadable_recovery_contract(runtime):
    from app.ai_auto.resume import _assess_pending_attempts

    member = {"instId": "BTC-USDT-SWAP", "verdict": "matched"}
    assessment = SimpleNamespace(
        row={"accountId": 1, "members": [member | {
            "runId": 1, "runtimeIntegrity": runtime}]},
        members=[member], creds=None)
    await _assess_pending_attempts(assessment)
    assert member.get("blockedReason") == "acceptance_unknown"


async def test_unknown_pending_is_not_replaced_by_older_adopted_order():
    executor, manager = await _partial_open()
    assert await manager.complete_adoption(executor)
    snapshot = manager.runtime_payload() | {"pendingOpenAttempt": {}}
    restored = OpenAttemptManager.restore(
        snapshot, account_id=1, run_id="run", inst_id=executor.inst_id,
        max_per_session=2)
    assert not restored.restore_resting(executor)
    assert restored.pending is None
    assert restored.runtime_payload()["pendingOpenAttempt"] == {}
