"""AgentManager 会话注册并发锁单测（H3）：同 convId 跨并发复用唯一会话，无脑裂。"""
from __future__ import annotations

import asyncio

from app.agent.manager import AgentManager
from app.agent.tools.registry import ToolRegistry
from app.config import Settings
from app.db.session import dispose_engine, get_sessionmaker, init_db


class _NoLLM:
    """不产事件的占位 LLM（本测仅触发会话注册路径，不发起 run）。"""

    async def stream(self, *, system, messages, tools):
        return
        yield  # pragma: no cover —— 使其成为 async generator


def _manager() -> AgentManager:
    return AgentManager(Settings(agent_api_key="x"), llm=_NoLLM(),
                        registry=ToolRegistry(), session_factory=get_sessionmaker())


async def test_concurrent_get_or_create_returns_same_session():
    await init_db()
    try:
        mgr = _manager()
        sessions = await asyncio.gather(*[mgr.get_or_create("conv-x") for _ in range(20)])
        first = sessions[0]
        assert all(s is first for s in sessions)  # 锁生效：唯一会话，无脑裂
    finally:
        await dispose_engine()


async def test_clear_rebuilds_fresh_session():
    await init_db()
    try:
        mgr = _manager()
        sess = await mgr.get_or_create("conv-y")
        await mgr.clear("conv-y")
        again = await mgr.get_or_create("conv-y")
        assert again is not sess  # clear（锁内 pop）后重建为新会话
    finally:
        await dispose_engine()
