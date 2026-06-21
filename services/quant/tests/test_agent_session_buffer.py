"""AgentSession 事件缓冲 / 续流原语单测（RS-1/RS-2/RN-2）：seq 注入 / 重放 / 空洞 / 心跳剥离。"""
from __future__ import annotations

from app.agent.loop import AgentSession, _event
from app.agent.tools.registry import ToolRegistry
from app.config import Settings
from app.db.session import get_sessionmaker


def _sess(**kw) -> AgentSession:
    settings = Settings(agent_api_key="x", **kw)
    return AgentSession("conv-buf", None, ToolRegistry(), settings, get_sessionmaker())


async def test_emit_injects_monotonic_seq_and_buffers():
    sess = _sess()
    for i in range(3):
        await sess._emit(_event("text_delta", {"text": f"x{i}"}))
    assert [seq for seq, _ in sess._buffer] == [1, 2, 3]
    assert all(ev["seq"] == seq for seq, ev in sess._buffer)


async def test_resume_to_replays_only_events_after_last_seq():
    sess = _sess()
    for i in range(5):
        await sess._emit(_event("text_delta", {"text": str(i)}))
    captured: list[dict] = []

    async def sink(ev):
        captured.append(ev)

    ok = await sess.resume_to(2, sink)
    assert ok is True
    assert [ev["seq"] for ev in captured] == [3, 4, 5]  # 仅 seq>2，按序，不重复


async def test_resume_to_gap_when_buffer_evicted_returns_false_without_partial():
    sess = _sess(agent_event_buffer_size=64)  # 下限，便于淘汰
    for i in range(100):
        await sess._emit(_event("text_delta", {"text": str(i)}))
    captured: list[dict] = []

    async def sink(ev):
        captured.append(ev)

    ok = await sess.resume_to(0, sink)   # lastSeq=0 落在保留窗口之前 → 空洞
    assert ok is False
    assert captured == []                # 绝不产出半截重放


async def test_resume_to_within_window_after_eviction_still_replays():
    sess = _sess(agent_event_buffer_size=64)
    for i in range(100):
        await sess._emit(_event("text_delta", {"text": str(i)}))
    captured: list[dict] = []

    async def sink(ev):
        captured.append(ev)

    ok = await sess.resume_to(90, sink)  # 90 在保留窗口内（最旧=37）
    assert ok is True
    assert [ev["seq"] for ev in captured] == list(range(91, 101))


async def test_notify_bypasses_seq_and_buffer():
    """notify / 心跳直发：不计 seq、不入缓冲、不带 seq 字段（RN-2）。"""
    sess = _sess()
    captured: list[dict] = []

    async def sink(ev):
        captured.append(ev)

    sess.set_sink(sink)
    await sess.notify(_event("heartbeat", {}))
    assert sess._seq == 0
    assert len(sess._buffer) == 0
    assert captured == [{"type": "heartbeat", "data": {}}]  # 无 seq 字段


async def test_seq_monotonic_across_runs_but_buffer_clears():
    """seq 跨 run 单调不重置（跨重连对账）；缓冲每 run 起始 clear（RS-1/R4）。"""
    sess = _sess()
    await sess._emit(_event("text_delta", {"text": "a"}))
    await sess._emit(_event("text_delta", {"text": "b"}))
    assert sess._seq == 2
    sess._buffer.clear()  # 模拟新 run 起始 clear（run() 内部行为）
    await sess._emit(_event("text_delta", {"text": "c"}))
    assert sess._seq == 3                            # seq 不重置
    assert [seq for seq, _ in sess._buffer] == [3]   # 缓冲只剩新 run 事件
