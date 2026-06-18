"""Process-local paper session manager."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from ..config import get_settings
from ..db import repositories as repo
from ..db.session import get_sessionmaker
from ..strategies.base import BaseStrategy
from .session import PaperSession


class PaperSessionLimitError(Exception):
    """Raised when starting sessions would exceed the configured cap."""


@dataclass
class PaperBatchStrategy:
    strategy_id: int
    strategy_name: str
    strategy: BaseStrategy


@dataclass
class PaperGroupSession:
    strategy_id: int
    strategy_name: str
    session_id: str
    run_id: int
    state: str


@dataclass
class PaperGroup:
    group_id: str
    inst_id: str
    bar: str
    sessions: list[PaperGroupSession]
    created_at: float


class PaperManager:
    """Runtime registry for live paper sessions and batch groups."""

    def __init__(self):
        self._sessions: dict[str, PaperSession] = {}
        self._session_meta: dict[str, dict] = {}
        self._groups: dict[str, PaperGroup] = {}

    async def start(self, strategy: BaseStrategy, strategy_id: int, inst_id: str,
                    bar: str, initial_capital: float, *, source: str = "live",
                    speed: float = 1.0, replay_bars: int = 300,
                    priming: bool = True, priming_bars: int = 300) -> PaperSession:
        self._ensure_capacity(1)

        sm = get_sessionmaker()
        async with sm() as s:
            run = await repo.create_run(
                s, strategy_id=strategy_id, inst_id=inst_id, bar=bar, mode="paper",
                config={"initialCapital": initial_capital, "source": source,
                        "speed": speed, "replayBars": replay_bars,
                        "priming": priming, "primingBars": priming_bars},
                status="running",
            )
            run_id = run.id

        session_id = uuid.uuid4().hex
        session = PaperSession(session_id, run_id, strategy, inst_id, bar,
                               initial_capital, source=source, speed=speed,
                               replay_bars=replay_bars, priming=priming,
                               priming_bars=priming_bars)
        self._sessions[session_id] = session
        self._session_meta[session_id] = {
            "strategyId": strategy_id,
            "runId": run_id,
            "groupId": None,
        }
        try:
            await session.start()
        except Exception:
            try:
                await session.stop()
            except Exception:
                pass
            self._sessions.pop(session_id, None)
            self._session_meta.pop(session_id, None)
            raise
        return session

    async def start_batch(self, strategies: list[PaperBatchStrategy], inst_id: str,
                          bar: str, initial_capital: float, *, source: str = "live",
                          speed: float = 1.0, replay_bars: int = 300,
                          priming: bool = True,
                          priming_bars: int = 300) -> PaperGroup:
        unique = dedupe_batch_strategies(strategies)
        self._ensure_capacity(len(unique))

        group_id = f"paper_{uuid.uuid4().hex}"
        group_sessions: list[PaperGroupSession] = []
        started_session_ids: list[str] = []
        try:
            for item in unique:
                session = await self.start(
                    item.strategy, item.strategy_id, inst_id, bar, initial_capital,
                    source=source, speed=speed, replay_bars=replay_bars,
                    priming=priming, priming_bars=priming_bars)
                started_session_ids.append(session.session_id)
                self._session_meta[session.session_id] = {
                    "strategyId": item.strategy_id,
                    "strategyName": item.strategy_name,
                    "runId": session.run_id,
                    "groupId": group_id,
                }
                group_sessions.append(PaperGroupSession(
                    strategy_id=item.strategy_id,
                    strategy_name=item.strategy_name,
                    session_id=session.session_id,
                    run_id=session.run_id,
                    state=session.state,
                ))
        except Exception:
            for session_id in started_session_ids:
                await self.stop(session_id)
                self._session_meta.pop(session_id, None)
            raise

        group = PaperGroup(group_id, inst_id, bar, group_sessions, time.time())
        self._groups[group_id] = group
        return group

    def get(self, session_id: str) -> PaperSession | None:
        return self._sessions.get(session_id)

    def get_group(self, group_id: str) -> PaperGroup | None:
        group = self._groups.get(group_id)
        if group is not None:
            self._refresh_group_states(group)
        return group

    async def stop(self, session_id: str) -> PaperSession | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        await session.stop()
        self._sync_group_session(session_id)
        return session

    async def stop_batch(self, group_id: str) -> PaperGroup | None:
        group = self._groups.get(group_id)
        if group is None:
            return None
        for entry in group.sessions:
            session = self._sessions.get(entry.session_id)
            if session is not None and session.state == "running":
                await session.stop()
            if session is not None:
                entry.state = session.state
        return group

    def list_sessions(self) -> list[dict]:
        rows = []
        for session_id, session in self._sessions.items():
            rows.append({**session.info(), **self._session_meta.get(session_id, {})})
        return rows

    async def shutdown(self) -> None:
        for session in list(self._sessions.values()):
            if session.state == "running":
                await session.stop()
        self._sessions.clear()
        self._session_meta.clear()
        self._groups.clear()

    def _ensure_capacity(self, requested_count: int) -> None:
        settings = get_settings()
        active_count = sum(1 for s in self._sessions.values() if s.state == "running")
        if active_count + requested_count > settings.max_paper_sessions:
            raise PaperSessionLimitError(
                f"active paper sessions exceed limit {settings.max_paper_sessions}")

    def _sync_group_session(self, session_id: str) -> None:
        meta = self._session_meta.get(session_id)
        group_id = meta.get("groupId") if meta else None
        group = self._groups.get(group_id) if group_id else None
        if group is not None:
            self._refresh_group_states(group)

    def _refresh_group_states(self, group: PaperGroup) -> None:
        for entry in group.sessions:
            session = self._sessions.get(entry.session_id)
            if session is not None:
                entry.state = session.state


def dedupe_batch_strategies(strategies: list[PaperBatchStrategy]) -> list[PaperBatchStrategy]:
    seen: set[int] = set()
    unique: list[PaperBatchStrategy] = []
    for item in strategies:
        if item.strategy_id in seen:
            continue
        seen.add(item.strategy_id)
        unique.append(item)
    return unique


_manager: PaperManager | None = None


def get_paper_manager() -> PaperManager:
    global _manager
    if _manager is None:
        _manager = PaperManager()
    return _manager
