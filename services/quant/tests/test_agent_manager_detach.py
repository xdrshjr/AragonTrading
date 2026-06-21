"""AgentManager 断连脱离 / 宽限 / 续流单测（RS-2/RS-3/RN-1）：run 归属会话、断连不取消。"""
from __future__ import annotations

import asyncio

from app.agent.llm.base import StreamEvent
from app.agent.manager import AgentManager
from app.agent.tools.registry import ToolRegistry
from app.config import Settings
from app.db.session import dispose_engine, get_sessionmaker, init_db


class _SlowLLM:
    """每个 text_delta 间 sleep，使 run 持续足够久供宽限 / 续流测。"""

    def __init__(self, n: int = 120, delay: float = 0.02):
        self.n = n
        self.delay = delay

    async def stream(self, *, system, messages, tools):
        for i in range(self.n):
            await asyncio.sleep(self.delay)
            yield StreamEvent(kind="text_delta", text=str(i))
        yield StreamEvent(kind="message_done", stop_reason="end_turn")


class _FastLLM:
    """一轮即自然完成（用于"宽限内完成"防回归）。"""

    async def stream(self, *, system, messages, tools):
        yield StreamEvent(kind="text_delta", text="done")
        yield StreamEvent(kind="message_done", stop_reason="end_turn")


async def _drop_sink(_ev):
    pass


def _mgr(llm, **kw) -> AgentManager:
    return AgentManager(Settings(agent_api_key="x", **kw), llm=llm,
                        registry=ToolRegistry(), session_factory=get_sessionmaker())


async def _start_slow_run(mgr: AgentManager, conv: str):
    await mgr.attach(conv, _drop_sink)
    await mgr.handle(conv, "hi", None)
    await asyncio.sleep(0.08)  # 让 run 起跑并产出若干事件
    return mgr._sessions[conv]


async def _teardown(mgr, sess):
    await mgr.shutdown()  # 取消宽限计时器 + 全部会话
    await AgentManager.drain(sess.run_task, 2.0)
    await dispose_engine()


async def test_detach_does_not_cancel_within_grace():
    await init_db()
    mgr = _mgr(_SlowLLM(), agent_detach_grace_seconds=5.0)
    sess = await _start_slow_run(mgr, "c1")
    try:
        assert sess.running is True
        await mgr.detach("c1", _drop_sink)
        await asyncio.sleep(0.1)
        assert sess.running is True and sess.cancelled is False  # 宽限内存活
    finally:
        await _teardown(mgr, sess)


async def test_grace_zero_cancels_immediately():
    await init_db()
    mgr = _mgr(_SlowLLM(), agent_detach_grace_seconds=0.0)
    sess = await _start_slow_run(mgr, "c2")
    try:
        await mgr.detach("c2", _drop_sink)
        await asyncio.sleep(0.12)  # grace=0：countdown 立即触发取消（等价迭代 2）
        assert sess.cancelled is True
    finally:
        await _teardown(mgr, sess)


async def test_grace_expiry_cancels_after_timeout():
    await init_db()
    mgr = _mgr(_SlowLLM(), agent_detach_grace_seconds=0.1)
    sess = await _start_slow_run(mgr, "c3")
    try:
        await mgr.detach("c3", _drop_sink)
        await asyncio.sleep(0.05)
        assert sess.cancelled is False  # 宽限内仍存活
        await asyncio.sleep(0.25)
        assert sess.cancelled is True   # 宽限耗尽取消
    finally:
        await _teardown(mgr, sess)


async def test_attach_within_grace_keeps_run_alive():
    await init_db()
    mgr = _mgr(_SlowLLM(), agent_detach_grace_seconds=0.2)
    sess = await _start_slow_run(mgr, "c4")
    try:
        await mgr.detach("c4", _drop_sink)
        await asyncio.sleep(0.05)
        await mgr.attach("c4", _drop_sink)  # 宽限内重连：取消计时器
        await asyncio.sleep(0.3)            # 超过原 0.2 宽限
        assert sess.running is True and sess.cancelled is False  # 存活（计时器已取消）
    finally:
        await _teardown(mgr, sess)


async def test_resume_running_replays_after_last_seq_and_acks_ok():
    await init_db()
    mgr = _mgr(_SlowLLM(), agent_detach_grace_seconds=5.0)
    sess = await _start_slow_run(mgr, "c5")
    try:
        last = sess._seq
        await mgr.detach("c5", _drop_sink)
        await asyncio.sleep(0.06)  # 断连期间继续产事件入缓冲
        cap: list[dict] = []

        async def sink(ev):
            cap.append(ev)

        await mgr.attach("c5", sink)        # running + has_buffered → 不立即设 sink
        await mgr.resume("c5", last, sink)
        resumed = [e for e in cap if e["type"] == "resumed"]
        assert resumed and resumed[0]["data"]["ok"] is True
        replayed = [e for e in cap if e["type"] == "text_delta"]
        assert replayed and all(e["seq"] > last for e in replayed)  # 仅重放 seq>last
    finally:
        await _teardown(mgr, sess)


async def test_resume_after_completion_acks_false_completed_without_replay():
    """RN-1 防回归：run 在宽限内自然完成 → resume 得 ok:false/completed，绝不重放。"""
    await init_db()
    mgr = _mgr(_FastLLM(), agent_detach_grace_seconds=5.0)
    try:
        await mgr.attach("c6", _drop_sink)
        await mgr.handle("c6", "hi", None)
        await asyncio.sleep(0.1)  # 等 run 自然完成
        sess = mgr._sessions["c6"]
        assert sess.running is False
        cap: list[dict] = []

        async def sink(ev):
            cap.append(ev)

        await mgr.attach("c6", sink)
        await mgr.resume("c6", 0, sink)
        resumed = [e for e in cap if e["type"] == "resumed"]
        assert resumed and resumed[0]["data"] == {"ok": False, "reason": "completed"}
        assert not any(e["type"] == "text_delta" for e in cap)  # DB history 权威，不重放
    finally:
        await dispose_engine()
