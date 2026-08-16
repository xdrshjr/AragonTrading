"""AgentManager 断连脱离 / 宽限 / 续流单测（RS-2/RS-3/RN-1）：run 归属会话、断连不取消。"""
from __future__ import annotations

import asyncio
import time

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


_RUN_STATE_TIMEOUT_S = 10.0


async def _await_run_started(sess, timeout: float = _RUN_STATE_TIMEOUT_S) -> None:
    """等到 run 真的起跑（`sess.running` 为真）；**按墙钟死线**有界等待（与 spec D2 同一条纪律）。

    绝不能拿一个固定 `sleep(...)` 当等待预算：run 要经 aiosqlite 线程池落库，那是真实耗时，
    整套顺序跑或机器有负载时那个常数常常不够，断言就会在 run 还没就位时先执行。
    超时即返回（不抛），由调用方的断言把真失败暴露成断言错误而不是超时噪声。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if sess.running:
            return
        await asyncio.sleep(0.005)


async def _settle_run_task(sess) -> None:
    """把 run 任务**真正**收敛掉。

    `AgentManager.drain` 超时后只 `cancel()`、**不 await**（manager.py 的 drain）：被留下的任务
    仍握着 aiosqlite 的 greenlet，事件循环关闭后它会在**别的模块**里以 `GeneratorExit` 现形
    （实测：整批跑时红在 `test_agent_provider_api.py`，而那里与 agent run 毫无关系）。
    这与 spec D2 夹具、D4 `_cancel_background` 是同一条纪律：投递取消之后必须自己再等一次。
    """
    task = sess.run_task
    if task is None or task.done():
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def _mgr(llm, **kw) -> AgentManager:
    return AgentManager(Settings(agent_api_key="x", **kw), llm=llm,
                        registry=ToolRegistry(), session_factory=get_sessionmaker())


async def _start_slow_run(mgr: AgentManager, conv: str):
    await mgr.attach(conv, _drop_sink)
    await mgr.handle(conv, "hi", None)
    sess = mgr._sessions[conv]
    await _await_run_started(sess)  # 先确认 run 真的起跑（墙钟死线，而不是赌一个固定 sleep）
    await asyncio.sleep(0.08)       # 再给它时间产出若干事件（_SlowLLM 每 20ms 一条）
    return sess


async def _teardown(mgr, sess):
    await mgr.shutdown()  # 取消宽限计时器 + 全部会话
    await AgentManager.drain(sess.run_task, 2.0)
    await _settle_run_task(sess)    # drain 超时只投递取消，收敛必须自己做（见该助手的说明）
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
        sess = mgr._sessions["c6"]
        # 等 run **任务真正结束**（有界）：`AgentSession.run` 的 finally 先置 `_running=False`、
        # 再 await 心跳收尾，故只等那个标志会在任务仍挂起时就返回，而本用例随后就 dispose_engine。
        await AgentManager.drain(sess.run_task, _RUN_STATE_TIMEOUT_S)
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
        await mgr.shutdown()   # 本用例原本不关停 manager：宽限计时器会跨用例泄漏（同 spec D2 根因 2）
        await dispose_engine()
