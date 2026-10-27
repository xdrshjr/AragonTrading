"""有界队列 + 单一 drain task 的七条硬约束（ai-trade-audit-journal §2.2.2）。

这一层的全部设计目标是「审计需求绝不碰坏真实资金的交易」，故每条约束各断一条：
入队不抛 / 队满丢**新**条 / 高水位先卸 info 事件 / 批量提交 / 落库失败不冒泡 /
`stop()` 有界 drain 后**真的 await 了那个 task** / 总闸关时零装配。
"""
from __future__ import annotations

import asyncio

import pytest

from app.ai_auto.journal.records import (
    EventJournalInput,
    JournalScope,
    build_event_row,
)
from app.ai_auto.journal.writer import JournalWriter, WriterConfig


class _FakeSession:
    def __init__(self, sink: list, boom: bool = False) -> None:
        self._sink = sink
        self._boom = boom

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _sessionmaker_factory(sink: list, boom: bool = False):
    """返回一个「零参 → async_sessionmaker 替身」的工厂（与生产签名同形）。"""
    def factory():
        def maker():
            return _FakeSession(sink, boom)
        return maker
    return factory


@pytest.fixture(autouse=True)
def _patch_repo(monkeypatch):
    """把落库替换成一个可观察、可爆炸的替身（本文件只测队列语义，不测 SQL）。"""
    written: list[dict] = []
    state = {"boom": False}

    async def _write(session, rows):
        if state["boom"]:
            raise RuntimeError("disk full")
        written.extend(rows)
        return len(rows)

    from app.ai_auto.journal import writer as writer_mod
    monkeypatch.setattr(writer_mod.journal_repo, "write_journal_batch", _write)
    yield written, state


def _event(level: str = "info") -> dict:
    return build_event_row(JournalScope(group_id="grp-1"),
                           EventJournalInput(category="lifecycle", code="X", level=level))


def _writer(**over) -> JournalWriter:
    cfg = WriterConfig(**{"queue_max": 8, "batch_size": 4, "flush_interval_s": 0.01,
                          "drain_timeout_s": 0.5, **over})
    return JournalWriter(cfg, _sessionmaker_factory([]))


@pytest.mark.asyncio
async def test_disabled_writer_builds_nothing_and_notes_nothing():
    """总闸关 ⇒ 不建 task、不建队列、`note_*` 直接 return（决策轮逐字节回退）。"""
    writer = _writer(enabled=False)
    writer.start()
    writer.note_event(_event())
    stats = writer.stats()
    assert stats["enabled"] is False and stats["queue"]["depth"] == 0
    assert stats["counters"]["enqueued"] == 0
    await writer.stop()


@pytest.mark.asyncio
async def test_enqueue_before_start_never_raises():
    """`note_*` 对热路径的全部承诺就是「绝不抛」——包括还没 start 的时候。"""
    writer = _writer()
    writer.note_decision({"kind": "decision", "cols": {}})
    assert writer.stats()["counters"]["enqueued"] == 0


@pytest.mark.asyncio
async def test_full_queue_drops_the_new_row_not_the_old_ones(_patch_repo):
    """队满丢**新**条：丢旧条会静默改写历史且读侧无法察觉。"""
    writer = _writer(queue_max=2)
    writer.start()
    writer._task.cancel()                     # 掐掉 drain，让队列真的能被填满
    for index in range(5):
        writer.note_decision({"kind": "decision", "cols": {"round_no": index}})
    counters = writer.stats()["counters"]
    assert counters["enqueued"] == 2 and counters["dropped"] == 3
    assert counters["droppedDecisions"] == 3
    # 队里留下的必须是**最早**的两条
    assert [writer._queue.get_nowait()["cols"]["round_no"] for _ in range(2)] == [0, 1]
    await writer.stop()


@pytest.mark.asyncio
async def test_info_events_are_shed_first_above_the_watermark(_patch_repo):
    """两级卸载：水位 > 80% 时 info 事件让位给决策行（决策行是主产物，info 事件是配菜）。"""
    writer = _writer(queue_max=10)
    writer.start()
    writer._task.cancel()
    for _ in range(9):                        # 填到 90% 水位
        writer.note_decision({"kind": "decision", "cols": {}})
    writer.note_event(_event("info"))
    writer.note_event(_event("error"))        # 高等级绝不被卸
    counters = writer.stats()["counters"]
    assert counters["shedInfoEvents"] == 1
    assert counters["enqueued"] == 10
    await writer.stop()


@pytest.mark.asyncio
async def test_event_min_level_silences_lower_levels_without_counting_them_as_drops(_patch_repo):
    """`EVENT_MIN_LEVEL=warn` 是用户显式选择的静默，**不计入任何丢弃计数**。"""
    writer = _writer(event_min_level="warn")
    writer.start()
    writer.note_event(_event("info"))
    counters = writer.stats()["counters"]
    assert counters["enqueued"] == 0 and counters["dropped"] == 0
    await writer.stop()


@pytest.mark.asyncio
async def test_batch_commit_groups_rows(_patch_repo):
    written, _state = _patch_repo
    writer = _writer(batch_size=4)
    writer.start()
    for index in range(4):
        writer.note_decision({"kind": "decision", "cols": {"round_no": index}})
    for _ in range(50):
        if len(written) >= 4:
            break
        await asyncio.sleep(0.01)
    assert len(written) == 4
    assert writer.stats()["counters"]["written"] == 4
    await writer.stop()


@pytest.mark.asyncio
async def test_write_failure_is_counted_and_never_bubbles(_patch_repo):
    """落库失败不重试、不冒泡、丢弃该批——一个会无限重试的审计写入会在磁盘满时拖死后端。"""
    _written, state = _patch_repo
    state["boom"] = True
    writer = _writer()
    writer.start()
    writer.note_decision({"kind": "decision", "cols": {}})
    for _ in range(50):
        if writer.stats()["counters"]["writeErrors"]:
            break
        await asyncio.sleep(0.01)
    assert writer.stats()["counters"]["writeErrors"] == 1
    assert not writer._task.done(), "drain 循环不得被一批失败打死"
    await writer.stop()


@pytest.mark.asyncio
async def test_stop_awaits_the_drain_task(_patch_repo):
    """只 cancel 不 await 是本仓库已经踩过的坑（`GeneratorExit` 会在别的模块里报红）。"""
    writer = _writer()
    writer.start()
    task = writer._task
    await writer.stop()
    assert task.done(), "stop() 必须 await 那个 task，而不只是 cancel 它"


@pytest.mark.asyncio
async def test_shutdown_drop_is_counted_and_recorded(_patch_repo):
    """关停时没排空的条数计入 `droppedOnShutdown`，并如实落一条事件行——「没写完」要被看见。"""
    written, _state = _patch_repo
    writer = _writer(queue_max=8, drain_timeout_s=0.0)
    writer.start()
    writer._task.cancel()
    for _ in range(3):
        writer.note_decision({"kind": "decision", "cols": {}})
    await writer.stop()
    assert writer.stats()["counters"]["droppedOnShutdown"] == 3
    codes = [row["cols"]["code"] for row in written if row.get("kind") == "event"]
    assert "SHUTDOWN_DROP" in codes


@pytest.mark.asyncio
async def test_reset_clears_counters_for_the_next_test(_patch_repo):
    writer = _writer()
    writer.start()
    writer.note_decision({"kind": "decision", "cols": {}})
    writer.reset()
    assert writer.stats()["counters"]["enqueued"] == 0
    await asyncio.sleep(0)      # 让被 cancel 的 drain task 走完它的取消分支
