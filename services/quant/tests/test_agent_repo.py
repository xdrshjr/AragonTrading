"""Agent 消息仓储往返单测。"""
from __future__ import annotations

from app.db import repositories as repo
from app.db.session import dispose_engine, get_sessionmaker, init_db


async def test_append_list_clear_agent_messages_round_trip():
    await init_db()
    sm = get_sessionmaker()
    try:
        async with sm() as session:
            await repo.append_agent_message(session, "conv-A", "user", '[{"type":"text","text":"hi"}]', 100)
            await repo.append_agent_message(session, "conv-A", "assistant",
                                            '[{"type":"text","text":"yo"}]', 101)
            await repo.append_agent_message(session, "conv-B", "user", "[]", 102)

        async with sm() as session:
            rows = await repo.list_agent_messages(session, "conv-A")
        assert [r.role for r in rows] == ["user", "assistant"]  # 升序
        assert rows[0].created_ts == 100

        async with sm() as session:
            removed = await repo.clear_agent_messages(session, "conv-A")
        assert removed == 2

        async with sm() as session:
            rows_a = await repo.list_agent_messages(session, "conv-A")
            rows_b = await repo.list_agent_messages(session, "conv-B")
        assert rows_a == []
        assert len(rows_b) == 1  # 清空只作用于目标会话
    finally:
        await dispose_engine()
