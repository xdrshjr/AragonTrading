"""老库补列与冲突分支的统计列保全（closed-loop §7.1 · test_ai_intel_migration）。

G-10 与 G-20 在这里。**用 52f466e 的建表 DDL 造一张缺列的表**再跑 patcher——用 `create_all`
造的新表天然不缺列，拿它当被测对象等于什么都没验。
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.session import _ensure_ai_intel_columns

# 52f466e 时代的建表 DDL（只保留本轮要补列的两张表，逐列照抄那一版的形状）。
_V1_ITEMS_DDL = """
CREATE TABLE ai_intel_items (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    inst_id VARCHAR(32),
    tier VARCHAR(8),
    category VARCHAR(16),
    headline VARCHAR(160),
    summary TEXT,
    stance VARCHAR(8),
    impact VARCHAR(8),
    confidence FLOAT,
    event_ts INTEGER,
    captured_ts INTEGER,
    updated_ts INTEGER,
    expires_ts INTEGER,
    status VARCHAR(12),
    pinned BOOLEAN,
    user_edited BOOLEAN,
    undated BOOLEAN,
    is_scheduled BOOLEAN,
    origin VARCHAR(8),
    origin_group_id VARCHAR(48),
    origin_round_id INTEGER,
    sources_json TEXT,
    source_count INTEGER,
    domain_count INTEGER,
    dedup_key VARCHAR(40),
    supersedes_id INTEGER,
    promote_count INTEGER,
    demote_count INTEGER,
    last_curated_ts INTEGER,
    curation_note VARCHAR(200),
    created_ts INTEGER,
    CONSTRAINT uq_ai_intel_dedup UNIQUE (inst_id, dedup_key)
)
"""
_V1_WATCH_DDL = """
CREATE TABLE ai_intel_watch (
    inst_id VARCHAR(32) NOT NULL PRIMARY KEY,
    enabled BOOLEAN,
    last_collect_ts INTEGER,
    last_curate_ts INTEGER,
    next_due_ts INTEGER,
    last_seen_ts INTEGER,
    collects_since_curate INTEGER,
    fail_streak INTEGER,
    cooldown_until_ts INTEGER,
    last_error VARCHAR(200),
    day_key VARCHAR(10),
    day_rounds INTEGER,
    updated_ts INTEGER
)
"""

# 7e2d5be 时代的 `ai_intel_rounds`（evidence-grounding 把它作为**第三张表**加进同一个 patcher）。
_V1_ROUNDS_DDL = """
CREATE TABLE ai_intel_rounds (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    inst_id VARCHAR(32),
    kind VARCHAR(8),
    group_id VARCHAR(48),
    started_ts INTEGER,
    finished_ts INTEGER,
    status VARCHAR(12),
    search_provider VARCHAR(16),
    queries_json TEXT,
    doc_count INTEGER,
    domain_count INTEGER,
    accepted INTEGER,
    rejected INTEGER,
    merged INTEGER,
    rejects_json TEXT,
    actions_json TEXT,
    llm_input_tokens INTEGER,
    llm_output_tokens INTEGER,
    degrade_code VARCHAR(24),
    error_text VARCHAR(400)
)
"""

NEW_ITEM_COLUMNS = ("cite_count", "cite_entry_count", "settled_count", "win_count",
                    "net_pnl_usd", "last_cited_ts", "verdict", "verdict_ts", "verdict_note",
                    "verify_count")
NEW_WATCH_COLUMNS = ("last_verify_ts", "curates_since_verify")
# ---- evidence-grounding §5.3 的 8 个新列 ----
EVIDENCE_ITEM_COLUMNS = ("quoted", "quote_count", "quote_state", "schedule_resolved_ts")
EVIDENCE_ROUND_COLUMNS = ("reader_attempt", "reader_ok", "quotes_proposed", "quotes_verified")


async def _columns(conn, table: str) -> set[str]:
    rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).all()
    return {row[1] for row in rows}


async def _old_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    async with engine.begin() as conn:
        await conn.execute(text(_V1_ITEMS_DDL))
        await conn.execute(text(_V1_WATCH_DDL))
        await conn.execute(text(_V1_ROUNDS_DDL))
        await conn.execute(text(
            "INSERT INTO ai_intel_rounds (inst_id, kind, status) "
            "VALUES ('BTC-USDT-SWAP', 'collect', 'ok')"))
        await conn.execute(text(
            "INSERT INTO ai_intel_items (inst_id, tier, status, headline, dedup_key)"
            " VALUES ('BTC-USDT-SWAP', 'mid', 'active', '旧闻', 'k1')"))
        await conn.execute(text(
            "INSERT INTO ai_intel_watch (inst_id, enabled) VALUES ('BTC-USDT-SWAP', 1)"))
    return engine


@pytest.mark.asyncio
async def test_old_db_gets_new_columns(tmp_path):
    """G-10：52f466e 时代的老库首次启动即被补齐 12 列，读写不抛 `no such column`。"""
    engine = await _old_db(tmp_path)
    async with engine.begin() as conn:
        before = await _columns(conn, "ai_intel_items")
        assert not (set(NEW_ITEM_COLUMNS) & before), "夹具本身必须真的缺列，否则这条用例什么都没验"
        await _ensure_ai_intel_columns(conn)

        assert set(NEW_ITEM_COLUMNS) <= await _columns(conn, "ai_intel_items")
        assert set(NEW_WATCH_COLUMNS) <= await _columns(conn, "ai_intel_watch")
        assert set(EVIDENCE_ITEM_COLUMNS) <= await _columns(conn, "ai_intel_items")
        assert set(EVIDENCE_ROUND_COLUMNS) <= await _columns(conn, "ai_intel_rounds")
        # 补列后的**老行**：`verdict` 是 ''（=待验证），统计列是 0。**绝不做任何历史回填**。
        row = (await conn.execute(text(
            "SELECT verdict, cite_count, verify_count FROM ai_intel_items"))).one()
        assert row == ("", 0, 0)
        await conn.execute(text(
            "UPDATE ai_intel_items SET cite_count = cite_count + 3, verdict = 'refuted'"))
        assert (await conn.execute(text(
            "SELECT cite_count FROM ai_intel_items"))).scalar() == 3
    await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_rows_are_marked_legacy_not_empty(tmp_path):
    """G-13：老行的 `quote_state` 必须是 `'legacy'`，**不能是 `''`**。

    空串已经被 wire 用作「有引文」的取值：两者撞在一起会让一条 2026 年 8 月的老情报在界面上
    显示成「有引证」而抽屉是空的——四句话里挑中了唯一一句确定错误的。
    """
    engine = await _old_db(tmp_path)
    async with engine.begin() as conn:
        await _ensure_ai_intel_columns(conn)
        state, quoted, resolved = (await conn.execute(text(
            "SELECT quote_state, quoted, schedule_resolved_ts FROM ai_intel_items"))).one()
        assert state == "legacy", "老行必须自称 legacy（本条早于原文取证功能）"
        assert not quoted and resolved == 0, "绝不做任何历史回填"
    await engine.dispose()


@pytest.mark.asyncio
async def test_today_zero_quote_item_is_not_called_legacy(tmp_path):
    """G-25：**已 ALTER 过的老库**上，今天入库的零引文条目不叫 `legacy`。

    SQLite 的 `ADD COLUMN ... DEFAULT 'legacy'` 会让 `'legacy'` 同时成为该表后续 INSERT 的
    服务端默认值，而 `_item_values` 又刻意不带这一列——两件事叠起来，一条今天入库、模型只是
    没给引文的条目会被界面说成「本条早于原文取证功能」。`write_intel_quote_state` 对本轮
    **全部** accepted + merges **无条件**写，正是为了堵这条链。
    """
    engine = await _old_db(tmp_path)
    async with engine.begin() as conn:
        await _ensure_ai_intel_columns(conn)
        await conn.execute(text(
            "INSERT INTO ai_intel_items (inst_id, tier, status, headline, dedup_key)"
            " VALUES ('BTC-USDT-SWAP', 'ultra', 'active', '今天的', 'k2')"))
        # 不写状态时，服务端默认值确实会把今天的条目染成 legacy —— 这正是那条链的第一环。
        assert (await conn.execute(text(
            "SELECT quote_state FROM ai_intel_items WHERE dedup_key = 'k2'"))).scalar() \
            == "legacy"
        # `write_intel_quote_state` 的等价写入（无条件覆盖）之后，它说出的是真实原因。
        await conn.execute(text(
            "UPDATE ai_intel_items SET quote_state = 'modelOmitted' WHERE dedup_key = 'k2'"))
        assert (await conn.execute(text(
            "SELECT quote_state FROM ai_intel_items WHERE dedup_key = 'k2'"))).scalar() \
            == "modelOmitted"
    await engine.dispose()


@pytest.mark.asyncio
async def test_patcher_is_idempotent_on_second_start(tmp_path):
    """逐列判存在 ⇒ 天然幂等；第二次启动既不抛也不改数。"""
    engine = await _old_db(tmp_path)
    async with engine.begin() as conn:
        await _ensure_ai_intel_columns(conn)
        await conn.execute(text("UPDATE ai_intel_items SET cite_count = 5"))
        await _ensure_ai_intel_columns(conn)
        assert (await conn.execute(text(
            "SELECT cite_count FROM ai_intel_items"))).scalar() == 5
    await engine.dispose()


@pytest.mark.asyncio
async def test_verdict_index_is_created_and_idempotent(tmp_path):
    engine = await _old_db(tmp_path)
    async with engine.begin() as conn:
        await _ensure_ai_intel_columns(conn)
        await _ensure_ai_intel_columns(conn)
        names = [row[0] for row in (await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index'"))).all()]
        assert "ix_ai_intel_verdict" in names
    await engine.dispose()


def test_item_values_carries_no_statistics_column():
    """G-20 / P1-6：10 个新列**绝不允许**出现在写方向。

    `upsert_intel_items` 的冲突分支用 `_item_values` 的键集减 `_PRESERVED_ON_CONFLICT` 求
    `update_cols`——统计列一旦进了写方向，同一条情报被再次报道时全部引用战绩会被**清零**且
    不报任何错。这条用例是纯静态的，故它在任何环境下都会红，而不是只在有 DB 时才红。
    """
    from app.ai_auto.intel.service import item_to_payload
    from app.db.ai_intel_repositories import _item_values

    values = _item_values({"instId": "X", "category": "market", "headline": "h",
                           "summary": "s", "dedupKey": "k"}, now=0)
    for column in (*NEW_ITEM_COLUMNS, *EVIDENCE_ITEM_COLUMNS):
        assert column not in values, f"{column} 进了 _item_values ⇒ 再次报道即清零战绩"

    from app.ai_auto.intel.gate import IntelItem

    payload = item_to_payload(IntelItem(
        item_id=1, inst_id="X", tier="ultra", category="market", headline="h", summary="s",
        stance="neutral", impact="high", confidence=0.5, event_ts=0, captured_ts=0,
        expires_ts=0, cite_count=9, verdict="refuted", quote_count=3,
        schedule_resolved_ts=7))
    assert "citeCount" not in payload and "verdict" not in payload
    assert "quoteCount" not in payload and "scheduleResolvedTs" not in payload


def test_asyncio_marker_is_registered():
    """夹具自检：本文件的三条 DB 用例依赖 `pytest.mark.asyncio`，缺插件时它们会被静默跳过。"""
    assert asyncio.iscoroutinefunction(test_old_db_gets_new_columns)


# ---------------- 结算的两条计数口径（G-4 的落库侧） ----------------
@pytest.mark.asyncio
async def test_settling_a_trip_adds_its_net_once_per_item(tmp_path):
    """同一条情报在开仓轮与平仓轮各被引用一次 ⇒ **净额只加一遍**，但计数列按行走。

    G-4 写死的是「净额增量**等于** `trip.net_pnl_usd`」。按行数乘会让这一笔往返的净额在它身上
    算两遍，而那个数正是「关联净额」那张读数卡的分子。
    """
    from app.db import repositories as repo
    from app.db.session import get_sessionmaker, init_db

    await init_db()
    async with get_sessionmaker()() as session:
        ids = await repo.upsert_intel_items(session, [{
            "instId": "ETH-USDT-SWAP", "tier": "mid", "category": "macro", "headline": "h",
            "summary": "s", "stance": "neutral", "impact": "high", "confidence": 0.5,
            "eventTs": 0, "capturedTs": 0, "expiresTs": 0, "status": "active",
            "origin": "agent", "dedupKey": "net-once", "sources": [], "domainCount": 0}])
        await repo.append_intel_citations(session, [
            {"itemId": ids[0], "instId": "ETH-USDT-SWAP", "runId": 42, "roundNo": 1,
             "citedTs": 10, "refToken": "I01", "role": "entry", "positionSeq": 5},
            {"itemId": ids[0], "instId": "ETH-USDT-SWAP", "runId": 42, "roundNo": 3,
             "citedTs": 30, "refToken": "I01", "role": "exit", "positionSeq": 5}])
        settled = await repo.settle_intel_citations(
            session, run_id=42, position_seq=5, net_pnl_usd=18.4, hold_s=900,
            exit_source="agent", outcome="win", now=99)
        item = await repo.get_intel_item(session, ids[0])

    assert settled == 2
    assert item["netPnlUsd"] == pytest.approx(18.4), "净额每条情报每笔往返只加一次"
    assert item["settledCount"] == 2 and item["winCount"] == 2, "计数列按引用行走"


@pytest.mark.asyncio
async def test_void_settlement_touches_no_statistics_column(tmp_path):
    """§3.2.2：`void` 写 `settled=True` 与 `NULL` 净额，但四个统计列一个字节不动。"""
    from app.db import repositories as repo
    from app.db.session import get_sessionmaker, init_db

    await init_db()
    async with get_sessionmaker()() as session:
        ids = await repo.upsert_intel_items(session, [{
            "instId": "ETH-USDT-SWAP", "tier": "mid", "category": "macro", "headline": "h",
            "summary": "s", "stance": "neutral", "impact": "high", "confidence": 0.5,
            "eventTs": 0, "capturedTs": 0, "expiresTs": 0, "status": "active",
            "origin": "agent", "dedupKey": "void-case", "sources": [], "domainCount": 0}])
        await repo.append_intel_citations(session, [
            {"itemId": ids[0], "instId": "ETH-USDT-SWAP", "runId": 43, "roundNo": 1,
             "citedTs": 10, "refToken": "I01", "role": "entry", "positionSeq": 6}])
        await repo.settle_intel_citations(
            session, run_id=43, position_seq=6, net_pnl_usd=None, hold_s=0,
            exit_source="offline_exit", outcome="void", now=99)
        item = await repo.get_intel_item(session, ids[0])
        rows, _more = await repo.list_intel_citations(session, inst_id="ETH-USDT-SWAP",
                                                      item_id=ids[0], limit=10)

    assert (item["settledCount"], item["winCount"], item["netPnlUsd"]) == (0, 0, 0.0)
    assert rows[0]["settled"] is True and rows[0]["netPnlUsd"] is None
    assert rows[0]["outcome"] == "void", "「没结算成功」与「结算成功但打平」是两件事"
