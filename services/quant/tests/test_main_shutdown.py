"""关停期后台协程收敛（spec D4）：`_cancel_background` 的两条路径。

**只测该助手，绝不驱动真实 `lifespan(app)`**（spec D4 / 评审 P1-4）：lifespan 起步就会
`init_db()` 跑启动对账（跨用例污染，见 `tests/conftest.py`）、`_okx_link_sweeper` 起步即发真实
HTTP 对时、退出段还要逐个 `shutdown()` 八个进程级单例——一条「验证关停确定性」的用例不该把这些
全部拖进来，那反而会引入一条新的顺序依赖。
"""
from __future__ import annotations

import asyncio
import time

from app.main import _cancel_background


async def test_cancel_background_settles_cancel_safe_tasks():
    """cancel-safe 的常驻协程：函数返回时它必须**已经真的结束**，而不只是被投递了取消。"""
    task = asyncio.create_task(asyncio.sleep(30))
    started = time.monotonic()
    await _cancel_background((task,))
    assert task.done()                                   # 不是「已 cancel()」，是「已收敛」
    assert time.monotonic() - started < 1.0              # 且是立刻返回，不是等满死线


async def test_cancel_background_returns_even_when_a_task_swallows_cancellation():
    """吞掉 `CancelledError` 的协程不得把整条关停链堵死：超时即返回（不抛），任务仍未结束。"""
    stop = asyncio.Event()

    async def _stubborn() -> None:
        while not stop.is_set():
            try:
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                pass          # 故意吞掉：复刻「一个不肯停的协程」

    task = asyncio.create_task(_stubborn())
    await asyncio.sleep(0)    # 让它真的起跑，否则 cancel 会在第一次调度前就命中
    await _cancel_background((task,), timeout=0.05)
    assert task.done() is False          # 它确实没停下来——这是要被看见的事实
    stop.set()                           # 用例自己收掉它，绝不把新的泄漏留给下一条用例
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert task.done()
