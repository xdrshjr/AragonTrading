"""resolve_active_provider 三分支单测：库内默认（含 key）/ env 回落 / None。"""
from __future__ import annotations

from app.agent.providers import resolve_active_provider
from app.config import Settings
from app.db import repositories as repo
from app.db.session import dispose_engine, get_sessionmaker, init_db


def _settings(**kw) -> Settings:
    return Settings(**kw)


async def _clear_all_providers(session) -> None:
    for row in await repo.list_agent_providers(session):
        await session.delete(row)
    await session.commit()


async def test_resolves_db_default_with_key():
    await init_db()
    sm = get_sessionmaker()
    try:
        async with sm() as session:
            await _clear_all_providers(session)
            row = await repo.create_agent_provider(
                session, kind="custom",
                values=repo.ProviderValues(label="DB默认", api_standard="openai",
                                           base_url="https://db.test", api_key="k-db000001",
                                           model="db-model", user_agent="ua"))
            await repo.set_default_agent_provider(session, row.id)
        cfg = await resolve_active_provider(sm, _settings(agent_api_key="env-key"))
        assert cfg is not None
        assert cfg.provider_id == row.id and cfg.model == "db-model"
        assert cfg.api_key == "k-db000001"  # 取库行 key，而非 env
    finally:
        await dispose_engine()


async def test_falls_back_to_env_when_no_db_default():
    await init_db()
    sm = get_sessionmaker()
    try:
        async with sm() as session:
            await _clear_all_providers(session)  # 无任何行 → 无默认
        cfg = await resolve_active_provider(
            sm, _settings(agent_api_key="env-key", agent_model="env-model"))
        assert cfg is not None and cfg.provider_id is None
        assert cfg.api_key == "env-key" and cfg.model == "env-model"
    finally:
        await dispose_engine()


async def test_returns_none_when_unconfigured():
    await init_db()
    sm = get_sessionmaker()
    try:
        async with sm() as session:
            await _clear_all_providers(session)
        cfg = await resolve_active_provider(sm, _settings(agent_api_key=""))
        assert cfg is None
    finally:
        await dispose_engine()


async def test_db_default_without_key_falls_back_to_env():
    await init_db()
    sm = get_sessionmaker()
    try:
        async with sm() as session:
            await _clear_all_providers(session)
            row = await repo.create_agent_provider(
                session, kind="custom",
                values=repo.ProviderValues(label="无key默认", api_standard="openai",
                                           base_url="https://nokey.test", api_key="",
                                           model="x", user_agent="ua"))
            await repo.set_default_agent_provider(session, row.id)
        cfg = await resolve_active_provider(sm, _settings(agent_api_key="env-key"))
        # 库内默认无 key → 跳过，回落 env
        assert cfg is not None and cfg.provider_id is None and cfg.api_key == "env-key"
    finally:
        await dispose_engine()
