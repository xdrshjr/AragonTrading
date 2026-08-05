"""发现层管理器测试（strategy-factor-discovery §7.1）。

承重用例：
- **并发闸用同步 ``reserve()`` 预占**（评审 P1-3）：连发两个启动请求，第二个恒 409，
  且判据**与时序无关**——这正是 ``active_count()`` 判定修不掉的 TOCTOU。
- **启动路径任一步抛错后 ``release()`` 归还名额**（绝不泄漏）。
- **总闸关闭时一律 ``paused(disabled_by_config)`` 且不起后台任务**（评审 P1-8）。
"""
from __future__ import annotations

import json

from sqlalchemy import delete

from app.config import Settings
from app.db.models import DiscoveryCandidate, DiscoveryIteration, DiscoveryRun
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.discovery import repositories as repo
from app.discovery.manager import (
    PAUSE_DISABLED_BY_CONFIG,
    PAUSE_PROCESS_RESTART,
    DiscoveryManager,
    get_discovery_manager,
    reconcile_orphan_runs,
)

_TS = 1_700_000_000


async def _fresh():
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        await session.execute(delete(DiscoveryCandidate))
        await session.execute(delete(DiscoveryIteration))
        await session.execute(delete(DiscoveryRun))
        await session.commit()
    return sm


def _values(auto_resume: bool = True) -> repo.RunValues:
    return repo.RunValues(
        label="BTC 1H 挖掘", inst_id="BTC-USDT-SWAP", bar="1H", symbols=["BTC-USDT-SWAP"],
        config={"axes": ["factor"], "autoResume": auto_resume}, checkpoint={"iteration": 3},
        started_ts=_TS)


class _StubRun:
    """极简 run 桩：只提供并发闸与关停三段需要的接口（不触网、不起协程）。"""

    def __init__(self, run_id: int):
        self.run_id = run_id
        self.status = "running"
        self.pause_reason = ""
        self.cancelled = False
        self.forced = False
        self.joined = False

    @property
    def is_terminal(self) -> bool:
        return self.status in ("stopped", "succeeded", "failed")

    def request_cancel(self):
        self.cancelled = True
        self.status = "stopped"

    def request_pause(self, reason="user"):
        self.pause_reason = reason

    def request_resume(self):
        self.pause_reason = ""

    def force_cancel(self):
        self.forced = True

    async def join(self):
        self.joined = True

    def start(self):
        return None


def _stub_manager(monkeypatch, cap: int = 1) -> DiscoveryManager:
    manager = DiscoveryManager()
    monkeypatch.setattr("app.discovery.manager.get_settings",
                        lambda: Settings(discovery_max_runs=cap))
    return manager


# ============================ 并发闸（承重：同步预占） ============================
def test_reserve_is_synchronous_so_two_back_to_back_requests_cannot_both_pass(monkeypatch):
    """连发两个启动请求，第二个**恒**失败——判据与时序无关。

    ``active_count() < N`` 的写法在「判定 → await → 注册」之间可被另一个请求插入，
    这正是仓库 H-B3 修过的 TOCTOU；本用例是它不复活的机器判据。
    """
    manager = _stub_manager(monkeypatch, cap=1)
    assert manager.reserve() is True
    assert manager.reserve() is False      # 还没注册任何 run，但名额已被预占
    assert manager.active_count() == 0     # 预占**不**依赖 active_count


def test_release_returns_the_slot(monkeypatch):
    manager = _stub_manager(monkeypatch, cap=1)
    assert manager.reserve() is True
    manager.release()
    assert manager.reserve() is True
    manager.release()
    manager.release()                      # 多次归还不会把计数压成负数
    assert manager._reserved == 0


def test_cap_is_read_dynamically_not_cached(monkeypatch):
    """cap 动态读 settings：缓存会与运行期覆写脱钩，让并发回归测试失去可控性。"""
    manager = DiscoveryManager()
    monkeypatch.setattr("app.discovery.manager.get_settings",
                        lambda: Settings(discovery_max_runs=1))
    assert manager.reserve() and not manager.reserve()
    monkeypatch.setattr("app.discovery.manager.get_settings",
                        lambda: Settings(discovery_max_runs=3))
    assert manager.reserve() is True


def test_active_count_ignores_terminal_runs(monkeypatch):
    manager = _stub_manager(monkeypatch, cap=2)
    manager._runs[1] = _StubRun(1)
    manager._runs[2] = _StubRun(2)
    manager._runs[2].status = "stopped"
    assert manager.active_count() == 1
    assert manager.active_run_ids() == {1}


# ============================ 生命周期 ============================
def test_pause_resume_stop_and_drop(monkeypatch):
    manager = _stub_manager(monkeypatch, cap=2)
    run = _StubRun(7)
    manager._runs[7] = run
    assert manager.pause(7, "user") == "paused" and run.pause_reason == "user"
    assert manager.resume(7) == "running" and run.pause_reason == ""
    assert manager.stop(7) == "stopping" and run.cancelled
    assert manager.stop(7) == "finished"           # 已终态 ⇒ 幂等
    assert manager.pause(99) == "unknown"
    manager.drop(7)
    assert manager.get(7) is None


async def test_shutdown_is_three_phase(monkeypatch):
    """关停三段：置标志 → 带超时 join → ``force_cancel`` 兜底。"""
    manager = _stub_manager(monkeypatch, cap=4)
    runs = [_StubRun(i) for i in range(3)]
    for run in runs:
        manager._runs[run.run_id] = run
    await manager.shutdown()
    assert all(run.cancelled and run.joined for run in runs)
    assert manager._runs == {}


# ============================ 启动对账（承重：总闸一票否决） ============================
async def test_reconcile_marks_running_orphans_resumable_when_enabled(monkeypatch):
    sm = await _fresh()
    try:
        async with sm() as session:
            row = await repo.create_run(session, values=_values(auto_resume=True))
        monkeypatch.setattr("app.discovery.manager.get_settings",
                            lambda: Settings(discovery_enabled=True,
                                             discovery_auto_resume=True))
        result = await reconcile_orphan_runs()
        assert result == [{"runId": row.id, "action": "resumable",
                           "pauseReason": PAUSE_PROCESS_RESTART}]
        async with sm() as session:
            refreshed = await repo.get_run(session, row.id)
            # 对账**先**一律置 paused：真正拉起由 lifespan 在静置延时后完成，绝不留幽灵 running。
            assert refreshed.status == "paused"
            assert refreshed.pause_reason == PAUSE_PROCESS_RESTART
    finally:
        await dispose_engine()


async def test_reconcile_is_idempotent_across_the_two_startup_calls(monkeypatch):
    """启动路径调它**两次**（``init_db`` 对账 + lifespan 自动续跑），两次必须给同一份名单。

    第一次已把 ``running`` 置成 ``paused``；若选取条件只认 ``running``，第二次一律空手而归，
    ``DISCOVERY_AUTO_RESUME`` 就成了一个永远不生效、且不报任何错的开关。
    """
    sm = await _fresh()
    try:
        async with sm() as session:
            row = await repo.create_run(session, values=_values(auto_resume=True))
        monkeypatch.setattr("app.discovery.manager.get_settings",
                            lambda: Settings(discovery_enabled=True,
                                             discovery_auto_resume=True))
        expected = [{"runId": row.id, "action": "resumable",
                     "pauseReason": PAUSE_PROCESS_RESTART}]
        assert await reconcile_orphan_runs() == expected     # init_db 的对账
        assert await reconcile_orphan_runs() == expected     # lifespan 的自动续跑
    finally:
        await dispose_engine()


async def test_master_switch_off_vetoes_auto_resume(monkeypatch):
    """总闸关闭 ⇒ 一律 ``paused(disabled_by_config)``，**绝不**拉起后台循环。

    否则写类端点 403 了、后台却继续烧 LLM 与 OKX 配额——「关了但没关」的假安全姿态。
    """
    sm = await _fresh()
    try:
        async with sm() as session:
            row = await repo.create_run(session, values=_values(auto_resume=True))
        monkeypatch.setattr("app.discovery.manager.get_settings",
                            lambda: Settings(discovery_enabled=False,
                                             discovery_auto_resume=True))
        result = await reconcile_orphan_runs()
        assert result == [{"runId": row.id, "action": "paused",
                           "pauseReason": PAUSE_DISABLED_BY_CONFIG}]
        async with sm() as session:
            refreshed = await repo.get_run(session, row.id)
            assert refreshed.pause_reason == PAUSE_DISABLED_BY_CONFIG
        assert get_discovery_manager().active_run_ids() == set()   # 没起任何后台任务
    finally:
        await dispose_engine()


async def test_run_level_auto_resume_false_also_stays_paused(monkeypatch):
    """判定顺序固定：总闸 → run.autoResume → 续跑，任一为假即停在 paused。"""
    sm = await _fresh()
    try:
        async with sm() as session:
            await repo.create_run(session, values=_values(auto_resume=False))
        monkeypatch.setattr("app.discovery.manager.get_settings",
                            lambda: Settings(discovery_enabled=True,
                                             discovery_auto_resume=True))
        result = await reconcile_orphan_runs()
        assert result[0]["action"] == "paused"
        assert result[0]["pauseReason"] == PAUSE_PROCESS_RESTART
    finally:
        await dispose_engine()


async def test_global_auto_resume_off_also_stays_paused(monkeypatch):
    sm = await _fresh()
    try:
        async with sm() as session:
            await repo.create_run(session, values=_values(auto_resume=True))
        monkeypatch.setattr("app.discovery.manager.get_settings",
                            lambda: Settings(discovery_enabled=True,
                                             discovery_auto_resume=False))
        assert (await reconcile_orphan_runs())[0]["action"] == "paused"
    finally:
        await dispose_engine()


async def test_reconcile_ignores_non_running_rows(monkeypatch):
    sm = await _fresh()
    try:
        async with sm() as session:
            row = await repo.create_run(session, values=_values())
            await repo.set_run_status(session, row.id, status="stopped")
        monkeypatch.setattr("app.discovery.manager.get_settings", lambda: Settings())
        assert await reconcile_orphan_runs() == []
    finally:
        await dispose_engine()


def test_malformed_config_json_is_treated_as_no_auto_resume():
    """配置 JSON 损坏 ⇒ 保守判为不自动续跑（安全偏向，绝不在不确定时拉起长跑）。"""
    from app.discovery.manager import _auto_resume_of

    class _Row:
        config_json = "not json"

    assert _auto_resume_of(_Row()) is False
    assert _auto_resume_of(type("R", (), {"config_json": json.dumps({})})()) is True
