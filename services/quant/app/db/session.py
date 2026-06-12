"""异步 DB 引擎 / 会话、建表、seed 内置 5 策略 (spec §8.3 / §5.1)。"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..strategies import BUILTIN_ORDER, STRATEGY_REGISTRY
from .models import Base, Strategy

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _db_url() -> str:
    settings = get_settings()
    db_path = Path(settings.db_path)
    if not db_path.is_absolute():
        # 相对路径：以 services/quant 为根（本文件位于 app/db/ 下，上溯两级）
        db_path = Path(__file__).resolve().parents[2] / settings.db_path
    return f"sqlite+aiosqlite:///{db_path.as_posix()}"


def get_engine():
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine(_db_url(), echo=False, future=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：产出一个 async session。"""
    sm = get_sessionmaker()
    async with sm() as session:
        yield session


async def init_db() -> None:
    """建表 + seed 内置策略（幂等）。"""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _seed_builtin_strategies()


async def _seed_builtin_strategies() -> None:
    """为注册表中每个模板确保存在一行 builtin 策略实例（按注册顺序）。"""
    sm = get_sessionmaker()
    async with sm() as session:
        result = await session.execute(select(Strategy.template_key).where(Strategy.builtin == True))  # noqa: E712
        existing = {row[0] for row in result.all()}
        keys = BUILTIN_ORDER + [k for k in STRATEGY_REGISTRY if k not in BUILTIN_ORDER]
        for key in keys:
            cls = STRATEGY_REGISTRY.get(key)
            if cls is None or key in existing:
                continue
            session.add(Strategy(
                template_key=key,
                name=cls.meta.name,
                params_json=json.dumps(cls.meta.default_params, ensure_ascii=False),
                enabled=True,
                builtin=True,
            ))
        await session.commit()


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None
