"""关停链与停止路径时延（ai-trade-experience-context-graph §4.9 / 门禁 G-5 / A-47）。

两条断言守的是同一件事：**真实资金的停止按钮不能被一个后台任务拖住，而后台任务也不能活着穿过
`dispose_engine`**。

A-47 正反两向：
· 反向——`drain_timeout_s >= shutdown_step_timeout_s` 的配置**拒绝构造**（倒挂时失败完全静默，
  日志里只多一行「shutdown step … did not settle」）；
· 正向——默认配置下把一个 `sleep(120)` 塞进任务集，`drain_experience_tasks()` 在 6 秒内返回且
  该任务已 `cancelled()`。
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.ai_auto.experience import scheduler
from app.ai_auto.manager import get_ai_trade_manager
from app.config import Settings


def test_a47_inverted_drain_deadline_is_refused_at_construction():
    """A-47 反向 · 死线倒挂 ⇒ `Settings` **拒绝构造**。

    倒挂时 `main._run_shutdown` 的单步死线（20s）会先 cancel 掉 drain，drain 自己那半段
    「cancel 剩余任务」**永远不会执行**——恰恰在它被设计来应对的那一种情况下。
    那些后台任务于是活着穿过 `("db_engine", dispose_engine)`：SQLite 引擎在还有活任务持着会话
    的情况下被 dispose。
    """
    with pytest.raises(ValueError, match="DRAIN_TIMEOUT_S"):
        Settings(shutdown_step_timeout_s=5.0, ai_trade_experience_drain_timeout_s=5.0)
    with pytest.raises(ValueError, match="DRAIN_TIMEOUT_S"):
        Settings(shutdown_step_timeout_s=3.0, ai_trade_experience_drain_timeout_s=9.0)
    # 正常配置照常构造（默认 5.0 < 20.0）。
    assert Settings().ai_trade_experience_drain_timeout_s < Settings().shutdown_step_timeout_s


async def test_a47_drain_cancels_first_then_converges_within_the_deadline():
    """A-47 正向 · **先 cancel、再有界收敛**（与 `main._cancel_background` 同姿态）。

    退出软件时没有任何理由再等一次 45 秒的 LLM 调用：确定性事件在这之前**已经落盘**，
    蒸馏是加法，图可以随时从事件重建。
    """
    manager = get_ai_trade_manager()
    slow = asyncio.ensure_future(asyncio.sleep(120))
    scheduler._TASKS.add(slow)
    try:
        started = time.monotonic()
        await manager.drain_experience_tasks()
        elapsed = time.monotonic() - started
        assert elapsed < 6.0, f"drain 花了 {elapsed:.1f}s——它应当先 cancel 再有界收敛"
        assert slow.cancelled(), "drain 没有 cancel 掉在飞任务（它会活着穿过 dispose_engine）"
    finally:
        scheduler._TASKS.discard(slow)
        if not slow.done():
            slow.cancel()


async def test_drain_is_a_noop_when_nothing_is_in_flight():
    """无在飞任务 ⇒ 立即返回（关停链的每一步都不该无谓地多等一次）。"""
    scheduler._TASKS.clear()
    started = time.monotonic()
    await get_ai_trade_manager().drain_experience_tasks()
    assert time.monotonic() - started < 0.5


async def test_g5_scheduling_experience_work_never_blocks_the_caller():
    """G-5 · 后台任务是 **spawn 而不是 await**：调度一个 120 秒的协程必须立刻返回。

    这是「物化与蒸馏不在停止链上」的机械判据——没有它，那句话只是一个口号。
    `manager.stop_group` 串行遍历成员，客户端 `/ai-trade/{groupId}/stop` 的超时档是
    `FAST_MS = 20_000` 且被判为写路径：一次 45 秒的 LLM 调用（或一次 5 秒的物化 ×3 成员）
    会让停止按钮弹出「本次操作结果未知」。**在真实资金的平仓按钮上制造一次假警报，
    代价远大于本特性的全部收益。**
    """
    manager = get_ai_trade_manager()
    scheduler._TASKS.clear()
    started = time.monotonic()
    for _ in range(3):                       # 3 成员分组的最坏形状
        manager._schedule_experience(asyncio.sleep(120))
    elapsed = time.monotonic() - started
    assert elapsed < 2.0, f"调度花了 {elapsed:.1f}s——它一定是被 await 了"
    assert len(scheduler.tasks()) == 3, "任务没有被 tracked ⇒ 关停时收不干净"
    # ★ 评审 P2-5：用例必须自己收尾。本仓库在 `a8ed650` 上为同形问题付过学费——drain 只 cancel
    # 不 await，`GeneratorExit` 红在**别的模块**里，排查成本远大于写这一行。
    await manager.drain_experience_tasks()
    assert not [t for t in scheduler.tasks() if not t.done()]


async def test_finished_tasks_remove_themselves_from_the_set():
    """`done_callback` 摘掉自己：长会话里的任务集不得单调增长。"""
    manager = get_ai_trade_manager()
    scheduler._TASKS.clear()

    async def _quick() -> None:
        return None

    manager._schedule_experience(_quick())
    await asyncio.sleep(0.05)
    assert len(scheduler.tasks()) == 0
