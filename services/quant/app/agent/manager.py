"""AgentManager：进程级单例，convId→AgentSession (spec §5.2)。

复用 paper/manager 的"模块级单例 + lifespan shutdown"模式。LLM 客户端 / 工具注册表懒构建，
便于测试注入。密钥缺失时 handle 直接回 AGENT_NOT_CONFIGURED，不下发前端。
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from ..config import Settings, get_settings
from ..db import repositories as repo
from ..db.session import get_sessionmaker
from .conversation import row_to_client_dict
from .llm.base import LLMClient
from .llm.factory import make_llm_client
from .loop import AgentSession
from .tools.registry import ToolRegistry

Emit = Callable[[dict], Awaitable[None]]


class AgentManager:
    """活动会话注册表 + 配置信息 + 持久化历史读取。"""

    def __init__(self, settings: Settings | None = None, *, llm: LLMClient | None = None,
                 registry: ToolRegistry | None = None,
                 session_factory: Callable | None = None):
        self._settings = settings or get_settings()
        self._registry = registry or ToolRegistry()
        self._llm = llm
        self._session_factory = session_factory
        self._sessions: dict[str, AgentSession] = {}
        # 会话注册表并发锁（H3）：保证每 convId 唯一会话，杜绝多连接脑裂。
        self._lock = asyncio.Lock()

    def _factory(self) -> Callable:
        return self._session_factory or get_sessionmaker()

    def _client(self) -> LLMClient:
        if self._llm is None:
            self._llm = make_llm_client(self._settings)
        return self._llm

    def configured(self) -> bool:
        return bool(self._settings.agent_api_key)

    def config_info(self) -> dict:
        return {"provider": self._settings.agent_provider, "model": self._settings.agent_model,
                "configured": self.configured()}

    async def get_or_create(self, conversation_id: str) -> AgentSession:
        """取或建会话：临界区加锁，确保同 convId 跨连接复用同一 AgentSession（H3，无脑裂）。"""
        async with self._lock:
            sess = self._sessions.get(conversation_id)
            if sess is None:
                sess = AgentSession(conversation_id, self._client(), self._registry,
                                    self._settings, self._factory())
                self._sessions[conversation_id] = sess
            return sess

    async def history(self, conversation_id: str) -> list[dict]:
        sm = self._factory()
        async with sm() as session:
            rows = await repo.list_agent_messages(session, conversation_id, limit=200)
        return [row_to_client_dict(r) for r in rows]

    async def handle(self, conversation_id: str, user_text: str, ui: dict | None,
                     emit: Emit) -> None:
        if not self.configured():
            await emit({"type": "error", "data": {
                "code": "AGENT_NOT_CONFIGURED",
                "message": "未配置模型密钥（AGENT_API_KEY）。请在 .env / userData/agent.env 填写后重试。"}})
            return
        sess = await self.get_or_create(conversation_id)
        if not sess.loaded:
            await sess.load_from_db()
        await sess.run(user_text, ui, emit)

    async def cancel(self, conversation_id: str) -> None:
        # 仅 .get() 读取，读安全、无需加锁（R4）。
        sess = self._sessions.get(conversation_id)
        if sess is not None:
            sess.cancel()

    async def clear(self, conversation_id: str) -> int:
        sm = self._factory()
        async with sm() as session:
            removed = await repo.clear_agent_messages(session, conversation_id)
        # clear 实为字典写入（pop），随 H3 一并纳入同一锁（RN-4）。
        async with self._lock:
            self._sessions.pop(conversation_id, None)
        return removed

    async def shutdown(self) -> None:
        for sess in self._sessions.values():
            sess.cancel()
        self._sessions.clear()


_manager: AgentManager | None = None


def get_agent_manager() -> AgentManager:
    global _manager
    if _manager is None:
        _manager = AgentManager()
    return _manager
