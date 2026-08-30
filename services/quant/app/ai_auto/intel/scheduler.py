"""情报层的后台任务调度（ai-trade-market-intel §3.5）。

**逐条镜像 `experience/scheduler.py` 立下的三条硬纪律**，不重新发明：

1. **绝不 await**（G-8）。`stop_group` 串行遍历成员，而客户端 `/ai-trade/{groupId}/stop` 的超时档
   是 `FAST_MS = 20_000` 且被判为写路径——一次 60 秒的情报轮会让**真实资金的停止按钮**弹出
   「本次操作结果未知」。故 `cancel()` 是**同步**的，只 cancel、不 await。
2. **关停先 cancel 再有界收敛**。`main._run_shutdown` 的单步死线是 `shutdown_step_timeout_s`
   （默认 20s），内层等得比它久 ⇒ 外层先 cancel ⇒ **内层的清理永远不执行**，后台任务活着穿过
   `dispose_engine`。死线由启动期校验器钉死 `ai_trade_intel_drain_timeout_s < shutdown_step_timeout_s`。
3. **未装配的会话不起任务**。`provider is None` ⇒ 不建任何后台任务、不开任何 DB 会话——一个持着
   锁、永远什么都采不到的任务只有害处。但 runtime 壳仍然注册（见 `runtime.py` 的模块 docstring）：
   纪律的**实质**（不起任务）保住了，可观测性没有被一起牺牲。

**每分组一个 tick 循环，绝不为每个标的起一个任务**：那会让 N 个标的的关停变成 N 个需要收敛的点。
"""
from __future__ import annotations

import asyncio
import logging

from ...db import repositories as repo
from . import service
from .runtime import IntelRuntime

logger = logging.getLogger(__name__)

# tracked 后台任务集（模块级，同 `experience/scheduler.py` 的理由）。
_TASKS: set[asyncio.Task] = set()
# 每分组一个调度循环。
_LOOPS: dict[str, asyncio.Task] = {}

# 到期扫描节拍。20 秒足够——采集周期的下限是 5 分钟，而更短的 tick 只会让一个空转的循环更频繁
# 地醒来。
TICK_SECONDS = 20.0


def tasks() -> set[asyncio.Task]:
    """在飞的情报层后台任务（只读；供时延门禁断言「它们仍在飞」）。"""
    return _TASKS


def schedule(coro) -> asyncio.Task:
    """把一个情报层协程登记成 tracked 后台任务（`done_callback` 摘掉自己，防泄漏）。"""
    task = asyncio.ensure_future(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


def start(runtime: IntelRuntime) -> None:
    """为一个分组起调度循环。未装配（`enabled=False`）⇒ **一个任务都不建**（纪律 3）。"""
    if not runtime.enabled:
        logger.info("ai-trade intel: 分组 %s 未配置检索源，只注册 runtime 壳、不起后台任务",
                    runtime.group_id)
        return
    existing = _LOOPS.get(runtime.group_id)
    if existing is not None and not existing.done():
        return
    task = schedule(_loop(runtime))
    _LOOPS[runtime.group_id] = task
    task.add_done_callback(_forget_loop(runtime.group_id))


def _forget_loop(group_id: str):
    """`_LOOPS` 的摘除回调（**比对身份再删**：慢任务收尾时该键可能已属于下一个循环了）。"""
    def forget(done: asyncio.Task) -> None:
        if _LOOPS.get(group_id) is done:
            del _LOOPS[group_id]

    return forget


def pause(group_id: str) -> None:
    """暂停调度——**只停调度、不取消在飞轮**，在飞轮跑完自然收敛。"""
    from .runtime import get_runtime

    runtime = get_runtime(group_id)
    if runtime is not None:
        runtime.paused = True


def resume(group_id: str) -> None:
    """恢复调度（`ai_intel_watch.next_due_ts` 让它接着上次的节奏跑）。"""
    from .runtime import get_runtime

    runtime = get_runtime(group_id)
    if runtime is not None:
        runtime.paused = False


def cancel(group_id: str) -> None:
    """**同步**。只 cancel，不 await（纪律 1）。"""
    task = _LOOPS.pop(group_id, None)
    if task is not None and not task.done():
        task.cancel()


async def drain(timeout: float) -> None:
    """关停：**先 cancel 全部、再有界收敛**（纪律 2）。绝不抛。"""
    pending = {task for task in _TASKS if not task.done()}
    if not pending:
        return
    for task in pending:
        task.cancel()                              # ① 先取消
    await asyncio.wait(pending, timeout=timeout)   # ② 有界收敛；收不干净就抛下它继续


async def _loop(runtime: IntelRuntime) -> None:
    """一个分组的到期扫描循环。**绝不把异常带出去**（后台任务抛出去只会变成一行无人读的 warning）。"""
    # 起步先把每个成员的快照拉一次：没有这一步，弹窗在第一轮采集完成之前拿不到任何计数，
    # 而库里可能已经有上一次运行攒下的情报（G-4 要保住的正是这个场景）。
    for inst_id in runtime.members:
        await runtime.refresh(inst_id)
    while True:
        try:
            await asyncio.sleep(TICK_SECONDS)
            if runtime.paused:
                continue
            await _tick(runtime)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 见 docstring
            logger.warning("ai-trade intel scheduler tick failed (%s): %s",
                           runtime.group_id, exc)


async def _tick(runtime: IntelRuntime) -> None:
    """扫一遍本分组成员，跑到期者（**串行**，一个 tick 至多推进一个标的一轮）。"""
    now = runtime.deps.now()
    async with runtime.deps.sessionmaker()() as db:
        watches = {inst_id: (await repo.load_intel_watch(db, inst_id) or {})
                   for inst_id in runtime.members}
    for inst_id, watch in watches.items():
        if not service.is_due(watch, now=now):
            continue
        outcome = await runtime.run_collect(inst_id)
        if outcome is None:
            continue
        every = int(runtime.deps.settings.ai_trade_intel_curate_every_n_collects)
        if int(watch.get("collectsSinceCurate") or 0) + 1 >= every:
            await runtime.run_curate(inst_id)
        return          # 一个 tick 只推进一个标的：N 个标的排队，绝不并发撞限流


def loops() -> dict[str, asyncio.Task]:
    """当前在跑的分组循环（只读；`test_ai_intel_wiring.py` 断言 start/stop 前后的增删）。"""
    return dict(_LOOPS)
