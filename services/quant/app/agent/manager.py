"""AgentManager：进程级单例，convId→AgentSession (spec §5.2)。

复用 paper/manager 的"模块级单例 + lifespan shutdown"模式。LLM 客户端 / 工具注册表懒构建，
便于测试注入。密钥缺失时 handle 直接回 AGENT_NOT_CONFIGURED，不下发前端。

韧性流式（iteration 3, RS-2/RS-3）：run 归属会话而非连接——`attach`/`detach`/`resume` 管理
sink 的热切换与断连宽限；断连即脱离（不取消），宽限内重连可续流，宽限耗尽才取消并落盘。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from ..config import Settings, get_settings
from ..db import repositories as repo
from ..db.session import get_sessionmaker
from .conversation import row_to_client_dict
from .llm.base import LLMClient
from .llm.factory import make_llm_client, make_llm_client_from_config
from .loop import AgentSession
from .providers import resolve_active_provider
from .tools.registry import ToolRegistry

logger = logging.getLogger("aragon.agent.manager")

Emit = Callable[[dict], Awaitable[None]]

# 单条用户输入长度护栏（RS-14）：边界一次性防御性截断，避免撑爆模型上下文（前端另有软校验）。
_MAX_USER_TEXT_CHARS = 16000


def _error(code: str, message: str) -> dict:
    return {"type": "error", "data": {"code": code, "message": message}}


def _resumed(ok: bool, *, reason: str | None = None, from_seq: int | None = None) -> dict:
    """构造 resumed 控制事件（不带 seq、不入缓冲）；reason/fromSeq 缺省即省略（RN-1）。"""
    data: dict = {"ok": ok}
    if reason is not None:
        data["reason"] = reason
    if from_seq is not None:
        data["fromSeq"] = from_seq
    return {"type": "resumed", "data": data}


def _retrieve_run_exception(task: asyncio.Task) -> None:
    """后台 run task 完成回调（RN-4）：检索并记录未处理异常，杜绝 "never retrieved" 噪音。"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("agent run task failed: %r", exc)


class AgentManager:
    """活动会话注册表 + 配置信息 + 持久化历史读取 + 续流宽限管理。"""

    def __init__(self, settings: Settings | None = None, *, llm: LLMClient | None = None,
                 registry: ToolRegistry | None = None,
                 session_factory: Callable | None = None):
        self._settings = settings or get_settings()
        self._registry = registry or ToolRegistry()
        self._llm = llm                       # 注入的测试桩（生产为 None，绝不自动赋值）
        self._env_client: LLMClient | None = None   # 懒建 env 回落 client（与注入桩分离）
        self._session_factory = session_factory
        self._sessions: dict[str, AgentSession] = {}
        # 会话注册表并发锁（H3）：保证每 convId 唯一会话，杜绝多连接脑裂。
        self._lock = asyncio.Lock()
        # 断连宽限计时器（RS-3）：convId → 延时取消任务；attach 命中即取消。
        self._grace_timers: dict[str, asyncio.Task] = {}

    def _factory(self) -> Callable:
        return self._session_factory or get_sessionmaker()

    def _client(self) -> LLMClient:
        """会话构造期的初始 client（注入桩优先，否则懒建 env 回落 client）。

        生产路径每轮 `run()` 会以解析出的当前默认服务**重新绑定** `self._llm`，故初始 client 仅占位。
        """
        if self._llm is not None:
            return self._llm
        if self._env_client is None:
            self._env_client = make_llm_client(self._settings)
        return self._env_client

    async def _resolve_client(self) -> LLMClient | None:
        """解析当前生效服务的 client；未配置返回 None（handle 据此回 AGENT_NOT_CONFIGURED）。

        注入桩路径（测试）配置判定回落 `settings.agent_api_key`（保持既有 env-only 测试语义），
        由桩控制 LLM 行为；生产路径走 DB 默认 → env → None 三级回落（每轮解析，毫秒级）。
        """
        if self._llm is not None:
            return self._llm if bool(self._settings.agent_api_key) else None
        cfg = await resolve_active_provider(self._factory(), self._settings)
        return make_llm_client_from_config(cfg) if cfg is not None else None

    async def active_config(self) -> dict:
        """当前生效服务摘要（WS ready / REST /agent/config 共用）；绝不返回 key（R3）。"""
        if self._llm is not None:
            return {"configured": bool(self._settings.agent_api_key),
                    "provider": self._settings.agent_provider, "model": self._settings.agent_model,
                    "providerId": None, "label": ""}
        cfg = await resolve_active_provider(self._factory(), self._settings)
        if cfg is None:
            return {"configured": False, "provider": self._settings.agent_provider,
                    "model": self._settings.agent_model, "providerId": None, "label": ""}
        return {"configured": True, "provider": cfg.api_standard, "model": cfg.model,
                "providerId": cfg.provider_id, "label": cfg.label}

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

    # ============================ 连接生命周期：attach / detach / resume（RS-2/RS-3） ===========
    async def attach(self, conversation_id: str, sink: Emit) -> AgentSession:
        """新连接挂载：取消待决宽限、挂载 sink（仅 fresh / run 未产事件时立即转实时，避免乱序）。

        正在跑且已产事件的会话**不立即设 sink**：交由后续 `resume` 原子重放后再转实时，
        杜绝重放期间实时事件抢先到达造成的乱序（详见 `AgentSession.resume_to`）。
        """
        sess = await self.get_or_create(conversation_id)
        self._cancel_grace(conversation_id)
        if not sess.running or not sess.has_buffered:
            sess.set_sink(sink)
        return sess

    async def detach(self, conversation_id: str, sink: Emit) -> None:
        """断连脱离（非取消，RS-2/RS-3）：置空本连接 sink；若仍在跑则起宽限计时器。"""
        sess = self._sessions.get(conversation_id)
        if sess is None:
            return
        sess.detach_sink(sink)
        if sess.running:
            self._start_grace(conversation_id)

    async def resume(self, conversation_id: str, last_seq: int, sink: Emit) -> None:
        """重连续流（RS-2/RN-1）：按 `sess.running` 严格分流，杜绝与 DB history 重复。"""
        sess = self._sessions.get(conversation_id)
        if sess is None:
            await AgentSession._safe_send(sink, _resumed(False, reason="none"))
            return
        if not sess.running:
            reason = "cancelled" if sess.cancelled else "completed"
            await AgentSession._safe_send(sink, _resumed(False, reason=reason))
            return
        ok = await sess.resume_to(last_seq, sink)
        await AgentSession._safe_send(
            sink, _resumed(ok, reason=None if ok else "gap", from_seq=last_seq if ok else None))

    # ============================ 宽限计时器（RS-3） ============================
    def _start_grace(self, conversation_id: str) -> None:
        grace = max(0.0, self._settings.agent_detach_grace_seconds)
        self._cancel_grace(conversation_id)
        self._grace_timers[conversation_id] = asyncio.create_task(
            self._grace_countdown(conversation_id, grace))

    def _cancel_grace(self, conversation_id: str) -> None:
        timer = self._grace_timers.pop(conversation_id, None)
        if timer is not None and not timer.done():
            timer.cancel()

    async def _grace_countdown(self, conversation_id: str, grace: float) -> None:
        """宽限计时：到点（含 grace=0 即立即）仍未 attach 则取消 run。被 attach 取消即提前返回。"""
        try:
            await asyncio.sleep(grace)
        except asyncio.CancelledError:
            return
        await self._grace_expired(conversation_id)

    async def _grace_expired(self, conversation_id: str) -> None:
        """宽限耗尽：取消 run（落盘已完成轮）+ 结构化日志，并有界 drain 收束孤儿任务。"""
        self._grace_timers.pop(conversation_id, None)
        sess = self._sessions.get(conversation_id)
        if sess is None or not sess.running:
            return
        sess.cancel()
        logger.info("agent run cancelled after detach grace: %s",
                    {"conv": conversation_id, "grace_s": self._settings.agent_detach_grace_seconds})
        await self.drain(sess.run_task, self._settings.agent_drain_timeout_seconds)

    @staticmethod
    async def drain(task: asyncio.Task | None, timeout: float) -> None:
        """有界等待 run task 收束（H4）：超时即 cancel 孤儿，清理路径永不挂死。

        宽限取消的兜底：先 ``sess.cancel()`` 协作式置位，再有界等 run 自然收束；超时则硬取消。
        """
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except asyncio.TimeoutError:
            task.cancel()
            logger.warning("agent run task drained by timeout (%.1fs); orphan cancelled", timeout)
        except Exception:  # noqa: BLE001 —— 取消路径兜底，吞掉后台任务残余异常
            logger.debug("agent run task ended with error during drain", exc_info=True)

    async def handle(self, conversation_id: str, user_text: str, ui: dict | None) -> None:
        """启动一轮 run：解析当前默认服务、把 client 传入 run（过 BUSY 守卫后才绑定，P1-Race）。"""
        sess = await self.get_or_create(conversation_id)
        client = await self._resolve_client()
        if client is None:
            await sess.notify(_error(
                "AGENT_NOT_CONFIGURED",
                "未配置模型密钥。请在设置中心填写并选择一个默认 AI 模型服务后重试。"))
            return
        if not sess.loaded:
            await sess.load_from_db()
        user_text = self._guard_user_text(user_text, conversation_id)
        task = asyncio.create_task(sess.run(user_text, ui, client=client))
        sess.bind_run_task(task)
        task.add_done_callback(_retrieve_run_exception)

    @staticmethod
    def _guard_user_text(user_text: str, conversation_id: str) -> str:
        """防御性截断超长用户输入（RS-14）：超限即裁到上限并记日志（仅元数据，C7）。"""
        if len(user_text) <= _MAX_USER_TEXT_CHARS:
            return user_text
        logger.warning("agent user_text truncated: %s",
                       {"conv": conversation_id, "len": len(user_text), "cap": _MAX_USER_TEXT_CHARS})
        return user_text[:_MAX_USER_TEXT_CHARS]

    async def cancel(self, conversation_id: str) -> None:
        # 仅 .get() 读取，读安全、无需加锁（R4）。
        sess = self._sessions.get(conversation_id)
        if sess is not None:
            sess.cancel()

    async def clear(self, conversation_id: str) -> int:
        sm = self._factory()
        async with sm() as session:
            removed = await repo.clear_agent_messages(session, conversation_id)
        self._cancel_grace(conversation_id)
        # clear 实为字典写入（pop），随 H3 一并纳入同一锁（RN-4）。
        async with self._lock:
            self._sessions.pop(conversation_id, None)
        return removed

    async def shutdown(self) -> None:
        for timer in self._grace_timers.values():
            if not timer.done():
                timer.cancel()
        self._grace_timers.clear()
        for sess in self._sessions.values():
            sess.cancel()
        self._sessions.clear()


_manager: AgentManager | None = None


def get_agent_manager() -> AgentManager:
    global _manager
    if _manager is None:
        _manager = AgentManager()
    return _manager
