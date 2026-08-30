"""情报层调度与关停链（§7.1 · test_ai_intel_scheduler）。

三条硬纪律各有一条用例：**绝不 await**（G-8）、**关停先 cancel 再有界收敛**、
**未装配的会话不起任务**（但 `no_provider` 仍要看得见，评审 P1-3）。
"""
from __future__ import annotations

import asyncio

import pytest

from app.ai_auto.intel import runtime as intel_runtime
from app.ai_auto.intel import scheduler as intel_scheduler
from app.ai_auto.intel import service
from app.ai_auto.intel.runtime import IntelRuntime
from app.ai_auto.intel.service import IntelDeps, backoff_seconds, is_due, next_due_after
from app.config import Settings

NOW = 1_800_000_000
INST = "BTC-USDT-SWAP"


@pytest.fixture(autouse=True)
def _clean_registry():
    """调度与注册表都是**模块级**单例：不清干净会跨用例污染（一个用例起的循环会活到下一个）。"""
    yield
    for task in list(intel_scheduler.tasks()):
        task.cancel()
    intel_scheduler._TASKS.clear()
    intel_scheduler._LOOPS.clear()
    intel_runtime._RUNTIMES.clear()
    intel_runtime._BY_INST.clear()


def _runtime(*, enabled=True, search=object(), llm=object()) -> IntelRuntime:
    deps = IntelDeps(settings=Settings(), sessionmaker=lambda: None,
                     search=search if enabled else None, llm=llm if enabled else None,
                     group_id="grp-1", bar="15m", now=(lambda: NOW))
    return IntelRuntime("grp-1", [INST], deps, enabled=enabled)


# ---------------- 到期 / 退避 / 日配额（纯函数） ----------------
def test_backoff_is_exponential_and_capped():
    assert backoff_seconds(0, base_s=120, max_s=3600) == 0
    assert backoff_seconds(1, base_s=120, max_s=3600) == 120
    assert backoff_seconds(2, base_s=120, max_s=3600) == 240
    assert backoff_seconds(3, base_s=120, max_s=3600) == 480
    assert backoff_seconds(20, base_s=120, max_s=3600) == 3600


def test_next_due_applies_jitter_bounds():
    lo = next_due_after(now=NOW, interval_min=30, jitter_pct=0.15, fail_streak=0,
                        base_s=120, max_s=3600, rng=lambda: 0.0)
    hi = next_due_after(now=NOW, interval_min=30, jitter_pct=0.15, fail_streak=0,
                        base_s=120, max_s=3600, rng=lambda: 1.0)
    assert lo == NOW + int(30 * 60 * 0.85)
    assert hi == NOW + int(30 * 60 * 1.15)


def test_is_due_respects_enabled_and_cooldown():
    assert is_due({"enabled": True, "nextDueTs": NOW - 1, "cooldownUntilTs": 0}, now=NOW)
    assert not is_due({"enabled": False, "nextDueTs": 0, "cooldownUntilTs": 0}, now=NOW)
    assert not is_due({"enabled": True, "nextDueTs": NOW + 10, "cooldownUntilTs": 0}, now=NOW)
    assert not is_due({"enabled": True, "nextDueTs": 0, "cooldownUntilTs": NOW + 10}, now=NOW)


def test_daily_used_resets_across_utc_day():
    from app.db import repositories as repo

    today = repo.intel_utc_day_key(NOW)
    assert service.daily_used({"dayKey": today, "dayRounds": 7}, now=NOW) == 7
    assert service.daily_used({"dayKey": "1999-01-01", "dayRounds": 7}, now=NOW) == 0


# ---------------- 装配纪律 ----------------
@pytest.mark.asyncio
async def test_no_task_when_provider_unconfigured_but_summary_still_visible():
    """评审 P1-3：不起任务的**实质**保住了，可观测性没有被一起牺牲。"""
    shell = _runtime(enabled=False)
    intel_runtime.register(shell)
    intel_scheduler.start(shell)
    assert intel_scheduler.loops() == {}, "未配置检索源时一个后台任务都不该建"
    summary = intel_runtime.intel_summary_for(INST)
    assert summary is not None
    assert summary["providerConfigured"] is False
    assert summary["degrade"]["code"] == "no_provider"
    # 未装配 ⇒ 注入端整键缺席 ⇒ 决策 prompt 逐字节回退。
    assert intel_runtime.intel_digest_for(INST) is None


@pytest.mark.asyncio
async def test_start_registers_a_single_loop_and_cancel_removes_it():
    runtime = _runtime()

    async def noop_loop(_runtime):
        await asyncio.sleep(3600)

    import app.ai_auto.intel.scheduler as sched

    original = sched._loop
    sched._loop = noop_loop
    try:
        intel_runtime.register(runtime)
        intel_scheduler.start(runtime)
        intel_scheduler.start(runtime)                 # 幂等：重复装配不该起第二个循环
        assert list(intel_scheduler.loops()) == ["grp-1"]
        intel_scheduler.cancel("grp-1")
        assert intel_scheduler.loops() == {}
    finally:
        sched._loop = original


@pytest.mark.asyncio
async def test_pause_only_stops_scheduling_not_in_flight_rounds():
    runtime = _runtime()
    intel_runtime.register(runtime)
    intel_scheduler.pause("grp-1")
    assert runtime.paused is True
    assert await runtime.run_collect(INST) is None, "暂停期不起新轮"
    intel_scheduler.resume("grp-1")
    assert runtime.paused is False


# ---------------- 关停链 ----------------
@pytest.mark.asyncio
async def test_cancel_is_synchronous_and_never_awaits_a_round():
    """G-8：挂一个永不返回的假情报轮，`cancel` 必须立刻返回。"""
    runtime = _runtime()

    async def forever(_runtime):
        await asyncio.sleep(3600)

    import app.ai_auto.intel.scheduler as sched

    original = sched._loop
    sched._loop = forever
    try:
        intel_runtime.register(runtime)
        intel_scheduler.start(runtime)
        loop = asyncio.get_running_loop()
        started = loop.time()
        intel_scheduler.cancel("grp-1")                # 同步调用，不 await
        assert loop.time() - started < 1.0
    finally:
        sched._loop = original


@pytest.mark.asyncio
async def test_drain_cancels_then_converges_within_deadline():
    """先 cancel 全部、再有界收敛：收不干净就抛下它继续，绝不抛异常。"""
    async def forever():
        await asyncio.sleep(3600)

    task = intel_scheduler.schedule(forever())
    await intel_scheduler.drain(1.0)
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_drain_with_no_tasks_is_a_noop():
    await intel_scheduler.drain(0.5)                   # 不该抛，也不该等


def test_drain_timeout_validator_rejects_inversion():
    """评审 P1-7：倒挂时外层先 cancel、内层清理永不执行——只能在构造期挡下来。"""
    with pytest.raises(ValueError) as err:
        Settings(ai_trade_intel_drain_timeout_s=30.0, shutdown_step_timeout_s=20.0)
    assert "AI_TRADE_INTEL_DRAIN_TIMEOUT_S" in str(err.value)
    # 正向：小于单步死线时正常构造。
    assert Settings(ai_trade_intel_drain_timeout_s=5.0,
                    shutdown_step_timeout_s=20.0).ai_trade_intel_drain_timeout_s == 5.0


# ---------------- 注册表 ----------------
def test_unregister_only_drops_its_own_symbols():
    """同一标的可能已经属于下一个分组了——按身份比对再删，不能按 key 盲删。"""
    first = _runtime()
    second = IntelRuntime("grp-2", [INST], first.deps, enabled=True)
    intel_runtime.register(first)
    intel_runtime.register(second)
    intel_runtime.unregister("grp-1")
    assert intel_runtime.runtime_for(INST) is second


@pytest.mark.asyncio
async def test_round_crash_is_contained_not_leaked_as_an_unretrieved_task():
    """轮入口**绝不抛**：一个逃出去的异常只会变成「Task exception was never retrieved」——
    既不自愈、也没有任何人看见。"""
    runtime = _runtime()

    async def boom(*args, **kwargs):
        raise RuntimeError("db gone")

    original = service.run_collect_round
    service.run_collect_round = boom
    try:
        assert await runtime.run_collect(INST) is None
    finally:
        service.run_collect_round = original


@pytest.mark.asyncio
async def test_paused_runtime_does_not_leave_a_dangling_coroutine():
    """暂停期早退时必须把没跑的协程关掉，否则每一次 tick 都留下一条 RuntimeWarning。"""
    runtime = _runtime()
    runtime.paused = True
    assert await runtime.run_collect(INST) is None
    assert await runtime.run_curate(INST) is None


def test_invalidate_is_a_noop_without_runtime():
    intel_runtime.invalidate("NOPE-USDT-SWAP")          # 不该抛
    assert intel_runtime.intel_summary_for("NOPE-USDT-SWAP") is None
