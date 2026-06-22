"""P1-Race：run 仅在通过内层 BUSY 守卫后才绑定传入 client；并发被拒轮不改写 in-flight client。"""
from __future__ import annotations

import asyncio

from app.agent.llm.base import StreamEvent
from app.agent.loop import AgentSession
from app.agent.tools.registry import ToolRegistry
from app.config import Settings
from app.db.session import dispose_engine, get_sessionmaker, init_db


class _GateLLM:
    """吐一段文本即结束，但开吐前阻塞在 release，便于在 run 进行中检查绑定。"""

    def __init__(self, tag: str):
        self.tag = tag
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(self, *, system, messages, tools):
        self.started.set()
        await self.release.wait()
        yield StreamEvent(kind="text_delta", text=self.tag)
        yield StreamEvent(kind="message_done", stop_reason="end_turn")


class _InstantLLM:
    def __init__(self, tag: str):
        self.tag = tag

    async def stream(self, *, system, messages, tools):
        yield StreamEvent(kind="text_delta", text=self.tag)
        yield StreamEvent(kind="message_done", stop_reason="end_turn")


def _settings() -> Settings:
    return Settings(agent_api_key="x", agent_provider="anthropic")


def _session(initial) -> AgentSession:
    return AgentSession("conv-bind", initial, ToolRegistry(), _settings(), get_sessionmaker())


async def test_fresh_run_binds_passed_client():
    await init_db()
    placeholder, chosen = _InstantLLM("placeholder"), _InstantLLM("chosen")
    sess = _session(placeholder)
    events: list[dict] = []

    async def emit(ev):
        events.append(ev)

    try:
        await sess.run("hi", None, emit, client=chosen)
        assert sess._llm is chosen  # 绑定为传入 client
        assert any(e["type"] == "text_delta" and e["data"]["text"] == "chosen" for e in events)
    finally:
        await dispose_engine()


async def test_concurrent_rejected_run_does_not_rebind_inflight_client():
    await init_db()
    first, second = _GateLLM("first"), _GateLLM("second")
    sess = _session(_InstantLLM("placeholder"))

    async def emit(_ev):
        pass

    sess.set_sink(emit)
    try:
        t1 = asyncio.create_task(sess.run("hi", None, client=first))
        await asyncio.wait_for(first.started.wait(), timeout=2.0)
        assert sess._llm is first  # 赢得 run 槽的一轮已绑定

        # 并发第二轮擦过 WS best-effort 检查进入 run → BUSY 守卫处被拒，绝不改写 in-flight client
        await sess.run("hi2", None, client=second)
        assert sess.running is True
        assert sess._llm is first  # 仍是 first，未被 second 替换

        first.release.set()
        await asyncio.wait_for(t1, timeout=2.0)
        assert sess._llm is first  # run 结束后仍是 first
    finally:
        await dispose_engine()
