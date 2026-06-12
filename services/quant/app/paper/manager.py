"""多会话注册 / 生命周期 (spec §6.4)，受 MAX_PAPER_SESSIONS 上限约束 (C11)。"""
from __future__ import annotations

import uuid

from ..config import get_settings
from ..db import repositories as repo
from ..db.session import get_sessionmaker
from ..strategies.base import BaseStrategy
from .session import PaperSession


class PaperSessionLimitError(Exception):
    """活跃会话已达上限。"""


class PaperManager:
    """进程级实时模拟会话管理器。"""

    def __init__(self):
        self._sessions: dict[str, PaperSession] = {}

    async def start(self, strategy: BaseStrategy, strategy_id: int, inst_id: str,
                    bar: str, initial_capital: float, *, source: str = "live",
                    speed: float = 1.0, replay_bars: int = 300) -> PaperSession:
        settings = get_settings()
        active = [s for s in self._sessions.values() if s.state == "running"]
        if len(active) >= settings.max_paper_sessions:
            raise PaperSessionLimitError(
                f"活跃会话已达上限 {settings.max_paper_sessions}")

        sm = get_sessionmaker()
        async with sm() as s:
            run = await repo.create_run(
                s, strategy_id=strategy_id, inst_id=inst_id, bar=bar, mode="paper",
                config={"initialCapital": initial_capital, "source": source,
                        "speed": speed, "replayBars": replay_bars}, status="running",
            )
            run_id = run.id

        session_id = uuid.uuid4().hex
        session = PaperSession(session_id, run_id, strategy, inst_id, bar, initial_capital,
                               source=source, speed=speed, replay_bars=replay_bars)
        self._sessions[session_id] = session
        await session.start()
        return session

    def get(self, session_id: str) -> PaperSession | None:
        return self._sessions.get(session_id)

    async def stop(self, session_id: str) -> PaperSession | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        await session.stop()
        return session

    def list_sessions(self) -> list[dict]:
        return [s.info() for s in self._sessions.values()]

    async def shutdown(self) -> None:
        for session in list(self._sessions.values()):
            if session.state == "running":
                await session.stop()
        self._sessions.clear()


_manager: PaperManager | None = None


def get_paper_manager() -> PaperManager:
    global _manager
    if _manager is None:
        _manager = PaperManager()
    return _manager
