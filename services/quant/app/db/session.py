"""异步 DB 引擎 / 会话、建表、seed 内置 5 策略 (spec §8.3 / §5.1)。"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import event, select
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
        _engine = create_async_engine(
            _db_url(),
            echo=False,
            future=True,
            connect_args={"timeout": 5},
        )
        event.listen(_engine.sync_engine, "connect", _configure_sqlite)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


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
    """建表 + seed 内置策略 + seed AI 模型服务商（均幂等）。"""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _seed_builtin_strategies()
    await _seed_agent_providers()


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


async def _seed_agent_providers() -> None:
    """仅空表时种入官方预置（openai/anthropic/gemini，空 key）+ `.env` KIMI 默认服务（幂等）。

    非空即跳过（保护用户在 UI 的增删改；桌面端每次启动注入 env 也不覆盖，R-Seed）。
    """
    # 延迟导入避免 db 模块层与 agent 层的循环依赖（db 不应在加载期依赖 agent）。
    from ..agent.providers import OFFICIAL_PRESETS
    from . import repositories as repo

    settings = get_settings()
    sm = get_sessionmaker()
    async with sm() as session:
        if await repo.count_agent_providers(session) > 0:
            return
        for preset in OFFICIAL_PRESETS:
            await repo.create_agent_provider(
                session, kind="official", slug=preset.slug,
                values=repo.ProviderValues(
                    label=preset.label, api_standard=preset.api_standard,
                    base_url=preset.base_url, api_key="", model=preset.model,
                    user_agent=preset.user_agent))
        # KIMI：.env 默认服务，自动种入并设为默认服务（任务要求；缺 key 时仍为默认，UI 引导补 key）。
        await repo.create_agent_provider(
            session, kind="official", slug="kimi",
            values=repo.ProviderValues(
                label="Kimi（内置默认）",
                api_standard=(settings.agent_provider or "anthropic").strip().lower(),
                base_url=settings.agent_base_url, api_key=settings.agent_api_key,
                model=settings.agent_model, user_agent=settings.agent_user_agent),
            is_default=True)


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None
