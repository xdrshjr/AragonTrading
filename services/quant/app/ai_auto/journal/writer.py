"""有界队列 + 单一 drain task 的日志写入器（ai-trade-audit-journal §2.2.2）。

**这一层的全部设计目标是「审计需求绝不碰坏真实资金的交易」**。七条硬约束：

1. **入队即已序列化**。`note_*` 收到的是 `records.build_*_row()` 的产物——里面已经全是标量与
   字符串。理由具体：`session._round_trace` 是原地累积的，把它的引用排进队列，落库时写下的是
   **下一轮**的内容。
2. **队满丢新条，绝不丢旧条、绝不阻塞**。丢旧条会**静默改写历史**且读侧无法察觉；丢新条则由
   `stats.dropped` 与管理台的琥珀色横幅明示。
3. **两级卸载**。水位 > 80% 时 `info` 级事件立即被丢弃并计入 `shedInfoEvents`，为决策行保留
   槽位——决策行是本特性的主产物，info 事件是配菜。
4. **drain 循环单例**：`await get()` 拿到第一条后贪心收集至多 `batch_size - 1` 条，一次
   `add_all + commit`。**绝不在决策协程里开事务**——SQLite 单写者，并发事务会把写锁竞争带进热路径。
5. **落库失败不重试、不冒泡**：`write_errors += 1` + warning + **丢弃该批**。一个会无限重试的
   审计写入，会在磁盘满的时候把整个后端拖死。
6. **`stop()` 必须先有界 drain、再 cancel、最后 `await` 那个 task**。只 cancel 不 await 是本
   仓库已经踩过的坑（`GeneratorExit` 会在别的模块里报红，见 `docs/plans/stability-hardening-audit/`）。
7. **总闸关闭时 `start()` 直接返回、`note_*` 直接 return**：不建 task、不建队列、零内存占用。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from ...db import ai_journal_repositories as journal_repo

logger = logging.getLogger(__name__)

# 两级卸载的水位（§2.2.2 约束 3）。0.8 不是随手取的：批量是 32，队列默认 2000，
# 20% 的余量足够 drain 在一次冲刷间隔内把积压吃掉，而 info 事件的丢弃是无损的。
SHED_WATERMARK = 0.8

# 关停时 SHUTDOWN_DROP 事件行的稳定机器码（§2.2.6 码表；改名 = 破坏性变更）。
SHUTDOWN_DROP_CODE = "SHUTDOWN_DROP"


@dataclass(frozen=True, slots=True)
class WriterConfig:
    """写入器的运行参数（由 `runtime.writer_config_from_settings` 从 `Settings` 派生）。"""

    enabled: bool = True
    queue_max: int = 2000
    batch_size: int = 32
    flush_interval_s: float = 1.0
    drain_timeout_s: float = 5.0
    event_min_level: str = "info"


_LEVEL_ORDER = {"info": 0, "warn": 1, "error": 2}


class JournalWriter:
    """进程级单例的日志写入器。`note_*` 同步、绝不抛；写库在独立 task 里。"""

    def __init__(self, config: WriterConfig, sessionmaker_factory) -> None:
        """
        Args:
            config: 运行参数。
            sessionmaker_factory: 零参可调用，返回一个 `async_sessionmaker`。
                **传工厂而不是 session**：drain task 的生命周期跨越无数个请求，
                持一个 session 会把它钉在一次事务上（本仓库在情报层已为此成文一次）。
        """
        self._config = config
        self._sessionmaker_factory = sessionmaker_factory
        self._queue: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None
        self._counters = {"enqueued": 0, "written": 0, "dropped": 0, "droppedDecisions": 0,
                          "droppedEvents": 0, "shedInfoEvents": 0, "writeErrors": 0,
                          "serializeErrors": 0, "droppedOnShutdown": 0}
        self._oldest_pending: float | None = None

    # ---------------------------------------------------------------- 生命周期

    @property
    def enabled(self) -> bool:
        return bool(self._config.enabled)

    def start(self) -> None:
        """幂等地拉起单一 drain task。总闸关 ⇒ 直接返回（不建 task、不建队列）。"""
        if not self._config.enabled or self._task is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:               # 没有运行中的事件循环（同步上下文）⇒ 不装配
            logger.warning("ai-trade journal writer not started: no running event loop")
            return
        self._queue = asyncio.Queue(maxsize=max(1, int(self._config.queue_max)))
        self._task = loop.create_task(self._drain_loop(), name="ai-trade-journal-drain")

    async def stop(self, timeout_s: float | None = None) -> None:
        """有界 drain → cancel → **await**（约束 6）。未排空的条数计入 `droppedOnShutdown`。"""
        task, queue = self._task, self._queue
        self._task, self._queue = None, None
        if task is None:
            return
        deadline = time.monotonic() + float(
            timeout_s if timeout_s is not None else self._config.drain_timeout_s)
        while queue is not None and not queue.empty() and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        remaining = queue.qsize() if queue is not None else 0
        task.cancel()
        try:
            await task                     # ★ 必须 await：只 cancel 不 await 会在别的模块里报红
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001 —— 关停期的任何异常都只记录，绝不向上冒泡
            logger.warning("ai-trade journal drain ended with %r", exc)
        if remaining:
            self._counters["droppedOnShutdown"] += remaining
            await self._write_shutdown_drop(remaining)

    def reset(self) -> None:
        """测试夹具用：清空计数与句柄（仿 okx-link-resilience 的四单例范式）。

        **同步地 cancel 掉在飞的 drain task**：不 cancel 就会留下一条 "Task was destroyed but
        it is pending" ——那是关停期最不需要的那种无出处噪声。这里只 cancel 不 await 是**因为
        本方法是同步的**；生产关停走的是 `stop()`，那条路径上 await 是硬约束。
        """
        if self._task is not None:
            self._task.cancel()
        self._task = None
        self._queue = None
        self._oldest_pending = None
        for key in self._counters:
            self._counters[key] = 0

    # ---------------------------------------------------------------- 入队（同步、绝不抛）

    def note_decision(self, row: dict) -> None:
        """入队一条决策行。**同步、绝不抛**——它跑在决策轮的 `try` 内。"""
        self._offer(row, kind="decision")

    def note_event(self, row: dict) -> None:
        """入队一条事件行。低于 `EVENT_MIN_LEVEL` 的整体不采；高水位时 info 级先被卸。"""
        level = row.get("level") or "info"
        if _LEVEL_ORDER.get(level, 0) < _LEVEL_ORDER.get(self._config.event_min_level, 0):
            return                          # 用户显式选择的静默，不计入任何丢弃计数
        self._offer(row, kind="event", level=level)

    def note_serialize_error(self) -> None:
        """写点的 `except` 分支唯一的副作用（§2.2.1）：只记一个数，绝不重抛。"""
        self._counters["serializeErrors"] += 1

    def _offer(self, row: dict, *, kind: str, level: str = "") -> None:
        queue = self._queue
        if not self._config.enabled or queue is None:
            return
        if kind == "event" and level == "info" and queue.qsize() > (
                queue.maxsize * SHED_WATERMARK):
            self._counters["shedInfoEvents"] += 1     # 约束 3：为决策行保留槽位
            return
        try:
            queue.put_nowait(row)
        except asyncio.QueueFull:
            self._counters["dropped"] += 1            # 约束 2：丢**新**条，绝不丢旧条
            self._counters["droppedDecisions" if kind == "decision" else "droppedEvents"] += 1
            return
        except Exception as exc:  # noqa: BLE001 —— note_* 绝不抛，这是它对热路径的全部承诺
            logger.warning("ai-trade journal enqueue failed: %s", exc)
            return
        self._counters["enqueued"] += 1
        if self._oldest_pending is None:
            self._oldest_pending = time.monotonic()

    # ---------------------------------------------------------------- 观测

    def stats(self) -> dict:
        """观测面（§4.6）。总闸关 ⇒ `enabled=False` 且队列读数恒为 0。"""
        queue = self._queue
        depth = queue.qsize() if queue is not None else 0
        oldest_ms = 0
        if depth and self._oldest_pending is not None:
            oldest_ms = int((time.monotonic() - self._oldest_pending) * 1000)
        return {"enabled": bool(self._config.enabled),
                "queue": {"depth": depth,
                          "max": queue.maxsize if queue is not None else 0,
                          "oldestPendingMs": oldest_ms},
                "counters": dict(self._counters)}

    # ---------------------------------------------------------------- drain

    async def _drain_loop(self) -> None:
        """单一 drain task：批量收集 → 一次事务提交。任何一批的失败都不得终止这个循环。"""
        while True:
            try:
                batch = await self._collect_batch()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 —— 收集侧的异常同样不得打死循环
                logger.warning("ai-trade journal batch collect failed: %s", exc)
                continue
            if not batch:
                continue
            await self._write_batch(batch)

    async def _collect_batch(self) -> list[dict]:
        """阻塞取第一条，再贪心收集至多 `batch_size - 1` 条（约束 4）。"""
        queue = self._queue
        if queue is None:
            await asyncio.sleep(self._config.flush_interval_s)
            return []
        first = await queue.get()
        batch = [first]
        for _ in range(max(0, int(self._config.batch_size) - 1)):
            try:
                batch.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        self._oldest_pending = None if queue.empty() else time.monotonic()
        return batch

    async def _write_batch(self, batch: list[dict]) -> None:
        """落库一批。**失败不重试、不冒泡、丢弃该批**（约束 5）。"""
        try:
            sessionmaker = self._sessionmaker_factory()
            async with sessionmaker() as session:
                written = await journal_repo.write_journal_batch(session, batch)
            self._counters["written"] += written
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 见约束 5
            self._counters["writeErrors"] += 1
            logger.warning("ai-trade journal batch write failed (%d rows): %s", len(batch), exc)

    async def _write_shutdown_drop(self, remaining: int) -> None:
        """把「关停时没写完 N 条」如实记成一条事件行。

        **落点在 `stop()` 内而不是下次启动**：关停步的位置由 §2.2.2 约束 6 钉死在
        `db_engine` **之前**，故此刻引擎仍在、这一次写必然写得出去；推迟到下次启动反而要引入
        一个新的跨进程状态文件（而进程被硬杀时那个文件同样写不出来）。
        「没写完」是要被看见的事实——这条写失败也只记 warning。
        """
        from .records import EventJournalInput, JournalScope, build_event_row
        row = build_event_row(JournalScope(), EventJournalInput(
            category="journal", code=SHUTDOWN_DROP_CODE, level="warn",
            message=f"关停时有 {remaining} 条日志未能写入，本时段的审计记录存在缺口",
            detail={"remaining": int(remaining)}))
        try:
            sessionmaker = self._sessionmaker_factory()
            async with sessionmaker() as session:
                await journal_repo.write_journal_batch(session, [row])
        except Exception as exc:  # noqa: BLE001 —— 关停期绝不抛
            logger.warning("ai-trade journal shutdown-drop notice not written: %s", exc)
