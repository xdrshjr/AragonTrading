"""AgentProvider 仓储层单测：CRUD 往返、set_default 唯一性、删默认回落、seed 幂等。

注：测试共享一个临时 DB（conftest 模块级），故每个用例先把 providers 表归到确定状态
（清空或清空后重新种子），避免跨用例污染。
"""
from __future__ import annotations

from app.db import repositories as repo
from app.db.session import dispose_engine, get_sessionmaker, init_db


def _values(label="自定义", standard="openai", url="https://x.test", key="k-abcd1234",
            model="m1", ua="claude-code/1.0") -> repo.ProviderValues:
    return repo.ProviderValues(label=label, api_standard=standard, base_url=url,
                               api_key=key, model=model, user_agent=ua)


async def _clear(sm) -> None:
    async with sm() as session:
        for row in await repo.list_agent_providers(session):
            await session.delete(row)
        await session.commit()


async def _empty_sm():
    """建表并清空 providers（确定空表起步）。"""
    await init_db()
    sm = get_sessionmaker()
    await _clear(sm)
    return sm


async def _seeded_sm():
    """清空后重新种入官方+KIMI（确定已种子起步）。"""
    await init_db()
    sm = get_sessionmaker()
    await _clear(sm)
    await init_db()  # 空表 → _seed_agent_providers 重新种
    return sm


async def test_create_list_update_delete_round_trip():
    sm = await _empty_sm()
    try:
        async with sm() as session:
            row = await repo.create_agent_provider(session, kind="custom", values=_values())
            assert row.id is not None and row.slug and row.kind == "custom"

            assert [r.id for r in await repo.list_agent_providers(session)] == [row.id]

            # 空 key 的 patch 保留原 key（前端不持有明文）
            updated = await repo.update_agent_provider(
                session, row.id, patch=repo.ProviderPatch(label="改名", api_key=""))
            assert updated.label == "改名" and updated.api_key == "k-abcd1234"

            # 非空 key 才覆盖
            updated2 = await repo.update_agent_provider(
                session, row.id, patch=repo.ProviderPatch(api_key="k-newkey99"))
            assert updated2.api_key == "k-newkey99"

            assert await repo.delete_agent_provider(session, row.id) == "ok"
            assert await repo.list_agent_providers(session) == []
    finally:
        await dispose_engine()


async def test_set_default_keeps_single_true():
    sm = await _empty_sm()
    try:
        async with sm() as session:
            a = await repo.create_agent_provider(session, kind="custom", values=_values(label="A"))
            b = await repo.create_agent_provider(session, kind="custom", values=_values(label="B"))
            assert await repo.set_default_agent_provider(session, a.id) is True
            assert await repo.set_default_agent_provider(session, b.id) is True
            rows = await repo.list_agent_providers(session)
            assert [r.id for r in rows if r.is_default] == [b.id]  # 仅最后设置的一行为默认
    finally:
        await dispose_engine()


async def test_delete_default_falls_back_to_lowest_id_with_key():
    sm = await _empty_sm()
    try:
        async with sm() as session:
            keyed = await repo.create_agent_provider(
                session, kind="custom", values=_values(label="有key", key="k-keep0001"))
            nokey = await repo.create_agent_provider(
                session, kind="custom", values=_values(label="无key", key=""))
            target = await repo.create_agent_provider(
                session, kind="custom", values=_values(label="待删默认", key="k-del00002"))
            await repo.set_default_agent_provider(session, target.id)

            assert await repo.delete_agent_provider(session, target.id) == "ok"
            new_default = await repo.get_default_agent_provider(session)
            # 回落到「含 key 且 id 最小」的行（keyed），跳过无 key 行
            assert new_default is not None and new_default.id == keyed.id
            assert nokey.id != new_default.id
    finally:
        await dispose_engine()


async def test_delete_official_rejected():
    sm = await _seeded_sm()
    try:
        async with sm() as session:
            official = await repo.get_agent_provider_by_slug(session, "kimi")
            assert official is not None
            assert await repo.delete_agent_provider(session, official.id) == "not_deletable"
    finally:
        await dispose_engine()


async def test_seed_is_idempotent():
    sm = await _seeded_sm()
    try:
        async with sm() as session:
            first = await repo.count_agent_providers(session)
        await init_db()  # 非空表 → 不重复种
        async with sm() as session:
            second = await repo.count_agent_providers(session)
        assert first == second and first >= 4  # openai/anthropic/gemini/kimi
    finally:
        await dispose_engine()


async def test_seed_marks_kimi_default():
    sm = await _seeded_sm()
    try:
        async with sm() as session:
            kimi = await repo.get_agent_provider_by_slug(session, "kimi")
            assert kimi is not None and kimi.is_default is True and kimi.kind == "official"
    finally:
        await dispose_engine()
