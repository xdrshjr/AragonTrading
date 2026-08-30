"""正文缓存与引文的数据层（§7.1 · test_ai_intel_evidence_repositories）。

真 SQLite（conftest 的临时库）。G-7 / G-8 在这里：**证据必须比缓存活得久**——一条 400 天有效期
的长期情报，它的原文不能因为 72 小时后缓存被清就消失。
"""
from __future__ import annotations

import time

import pytest
from sqlalchemy import delete, func, select

from app.ai_auto.intel.corpus import canonical_url, document_key
from app.db import repositories as repo
from app.db.models import AiIntelDocument, AiIntelItem, AiIntelQuote, AiIntelRound
from app.db.session import get_sessionmaker, init_db

NOW = int(time.time())
INST = "BTC-USDT-SWAP"
TTL_S = 72 * 3600.0
NEG_TTL_S = 6 * 3600.0


async def _fresh():
    await init_db()
    async with get_sessionmaker()() as session:
        # ⚠️ 本仓库的 pytest 共用**同一个临时库**：轮存根不清，别的用例写下的轮会把
        # `count_recent_quote_stats` 的近 20 轮窗口污染掉（症状是分子分母莫名其妙变大）。
        for table in (AiIntelDocument, AiIntelQuote):
            await session.execute(delete(table))
        await session.execute(delete(AiIntelRound).where(AiIntelRound.inst_id == INST))
        await session.execute(delete(AiIntelItem).where(AiIntelItem.inst_id == INST))
        await session.commit()


def _doc(url: str, *, status: str = "ok", body: str = "正文", fetched_ts: int = NOW) -> dict:
    return {"urlHash": document_key(url), "url": url, "finalUrl": url,
            "domain": "reuters.com", "title": "t", "body": body, "bodyChars": len(body),
            "byteLen": 100, "status": status, "httpStatus": 200, "fetchedTs": fetched_ts,
            "truncated": False}


# ---------------- 缓存命中与负缓存 ----------------
@pytest.mark.asyncio
async def test_cache_hit_within_ttl_is_returned():
    await _fresh()
    url = "https://reuters.com/a"
    async with get_sessionmaker()() as session:
        await repo.upsert_documents(session, [_doc(url)])
        hit = await repo.get_cached_documents(session, [document_key(url)], now=NOW,
                                              ttl_s=TTL_S, negative_ttl_s=NEG_TTL_S)
    assert hit[document_key(url)]["body"] == "正文"


@pytest.mark.asyncio
async def test_expired_positive_cache_is_not_returned():
    await _fresh()
    url = "https://reuters.com/a"
    async with get_sessionmaker()() as session:
        await repo.upsert_documents(session, [_doc(url, fetched_ts=NOW - int(TTL_S) - 10)])
        hit = await repo.get_cached_documents(session, [document_key(url)], now=NOW,
                                              ttl_s=TTL_S, negative_ttl_s=NEG_TTL_S)
    assert hit == {}


@pytest.mark.asyncio
async def test_negative_cache_uses_a_shorter_window():
    """一个 404 不该被反复重抓，但也不该被记住 72 小时。"""
    await _fresh()
    url = "https://reuters.com/gone"
    async with get_sessionmaker()() as session:
        await repo.upsert_documents(session, [
            _doc(url, status="http_error", body="", fetched_ts=NOW - int(NEG_TTL_S) + 60)])
        fresh = await repo.get_cached_documents(session, [document_key(url)], now=NOW,
                                                ttl_s=TTL_S, negative_ttl_s=NEG_TTL_S)
        assert fresh[document_key(url)]["status"] == "http_error"

        await repo.upsert_documents(session, [
            _doc(url, status="http_error", body="", fetched_ts=NOW - int(NEG_TTL_S) - 60)])
        stale = await repo.get_cached_documents(session, [document_key(url)], now=NOW,
                                                ttl_s=TTL_S, negative_ttl_s=NEG_TTL_S)
    assert stale == {}, "负缓存过期后必须重新出网"


@pytest.mark.asyncio
async def test_upsert_is_keyed_by_canonical_url():
    """归一函数与键绑在一起：带 query / 尾斜杠的同一篇文章只占一行。"""
    await _fresh()
    async with get_sessionmaker()() as session:
        await repo.upsert_documents(session, [_doc("https://reuters.com/a?utm=x")])
        await repo.upsert_documents(session, [_doc("https://reuters.com/a/")])
        total = (await session.execute(
            select(func.count()).select_from(AiIntelDocument))).scalar()
    assert total == 1
    assert canonical_url("https://reuters.com/a?utm=x") == canonical_url("https://reuters.com/a/")


# ---------------- 三道闸 ----------------
@pytest.mark.asyncio
async def test_prune_respects_ttl_rows_and_bytes():
    await _fresh()
    async with get_sessionmaker()() as session:
        await repo.upsert_documents(session, [
            _doc(f"https://reuters.com/old{i}", fetched_ts=NOW - int(TTL_S * 2) - 10)
            for i in range(3)])
        await repo.upsert_documents(session, [
            _doc(f"https://reuters.com/new{i}", body="正" * 50, fetched_ts=NOW - i)
            for i in range(5)])
        counts = await repo.prune_documents(session, now=NOW, ttl_s=TTL_S, max_rows=3,
                                            max_bytes=10_000_000)
        rows = (await session.execute(
            select(func.count()).select_from(AiIntelDocument))).scalar()
    assert counts["ttl"] == 3 and counts["rows"] == 2 and rows == 3


@pytest.mark.asyncio
async def test_byte_gate_counts_utf8_bytes_not_characters():
    """SQLite 的 `length()` 对 TEXT 返回**字符数**：少了 CAST，中文正文会被少算 3 倍。"""
    await _fresh()
    body = "中" * 100                                        # 100 字符 / 300 字节
    async with get_sessionmaker()() as session:
        await repo.upsert_documents(session, [
            _doc(f"https://reuters.com/x{i}", body=body, fetched_ts=NOW - i)
            for i in range(4)])
        health = await repo.document_health(session)
        counts = await repo.prune_documents(session, now=NOW, ttl_s=TTL_S, max_rows=100,
                                            max_bytes=700)
        left = (await session.execute(
            select(func.count()).select_from(AiIntelDocument))).scalar()
    assert health["bodyBytes"] == 4 * 300, "按字节算：4 × 300 = 1200，不是 400"
    assert counts["bytes"] == 1 and left == 3
    assert health["cachedDocs"] == 4


@pytest.mark.asyncio
async def test_prune_records_its_own_timestamp():
    await _fresh()
    async with get_sessionmaker()() as session:
        await repo.prune_documents(session, now=NOW, ttl_s=TTL_S, max_rows=10,
                                   max_bytes=1000)
        health = await repo.document_health(session)
    assert health["lastPrunedTs"] == NOW


# ---------------- 引文：页面失效免疫 ----------------
async def _item(dedup: str = "ev-1") -> int:
    async with get_sessionmaker()() as session:
        ids = await repo.upsert_intel_items(session, [{
            "instId": INST, "tier": "ultra", "category": "macro", "headline": "h",
            "summary": "s", "stance": "neutral", "impact": "high", "confidence": 0.5,
            "eventTs": NOW, "capturedTs": NOW, "expiresTs": NOW + 3600, "status": "active",
            "origin": "agent", "dedupKey": dedup, "sources": [
                {"url": "https://reuters.com/a", "title": "t", "publisher": "reuters.com",
                 "domain": "reuters.com", "publishedTs": NOW}],
            "domainCount": 1}])
    return ids[0]


@pytest.mark.asyncio
async def test_quotes_survive_document_prune():
    """G-7：清空整张文档表之后，引文照样读得到——**证据比缓存活得久**。"""
    await _fresh()
    item_id = await _item()
    async with get_sessionmaker()() as session:
        await repo.upsert_documents(session, [_doc("https://reuters.com/a")])
        await repo.write_intel_quotes(session, [{
            "itemId": item_id, "instId": INST, "roundId": 1, "role": "admit", "ref": "D1",
            "text": "核心 CPI 同比 3.1%", "sourceField": "body",
            "docUrl": "https://reuters.com/a", "docDomain": "reuters.com",
            "docPublishedTs": NOW, "charStart": 12, "hasImperative": False}])
        await session.execute(delete(AiIntelDocument))
        await session.commit()
        quotes = await repo.list_item_quotes(session, item_id)
        docs = await repo.list_documents_for_urls(
            session, [document_key("https://reuters.com/a")])
    assert len(quotes) == 1 and quotes[0]["text"] == "核心 CPI 同比 3.1%"
    assert docs == [], "缓存被淘汰 ⇒ 前端渲染「原文快照已过期，下方引文仍为当时逐字留存」"


@pytest.mark.asyncio
async def test_deleting_an_item_deletes_its_quotes():
    """G-8：删条目必删引文，且在**同一个事务**里（本仓库的表一律不建外键）。"""
    await _fresh()
    item_id = await _item("ev-2")
    async with get_sessionmaker()() as session:
        await repo.write_intel_quotes(session, [{
            "itemId": item_id, "instId": INST, "roundId": 1, "role": "admit", "ref": "D1",
            "text": "x" * 30, "sourceField": "body", "docUrl": "https://reuters.com/a",
            "docDomain": "reuters.com", "docPublishedTs": NOW}])
        assert await repo.delete_intel_item(session, item_id) is True
        left = (await session.execute(
            select(func.count()).select_from(AiIntelQuote))).scalar()
    assert left == 0


@pytest.mark.asyncio
async def test_quote_rows_without_a_host_are_dropped():
    """没有宿主的引文行在任何界面上都不可见，只会让表无限长。"""
    await _fresh()
    async with get_sessionmaker()() as session:
        written = await repo.write_intel_quotes(session, [{
            "itemId": 0, "instId": INST, "roundId": 1, "role": "admit", "ref": "D1",
            "text": "x" * 30, "sourceField": "body"}])
    assert written == 0


@pytest.mark.asyncio
async def test_quote_state_write_covers_zero_quote_items():
    await _fresh()
    item_id = await _item("ev-3")
    async with get_sessionmaker()() as session:
        await repo.write_intel_quote_state(session, {item_id: (False, 0, "modelOmitted")})
        row = await repo.get_intel_item(session, item_id)
    assert row["quoteState"] == "modelOmitted" and row["quoted"] is False


@pytest.mark.asyncio
async def test_recent_quote_stats_returns_none_when_nothing_was_proposed():
    """「没提过引文」与「提了全没中」在设置卡上是两句不同的话。"""
    await _fresh()
    async with get_sessionmaker()() as session:
        empty = await repo.count_recent_quote_stats(session, "NOBODY-USDT-SWAP")
        await repo.write_intel_round(session, {
            "instId": INST, "kind": "collect", "startedTs": NOW, "status": "ok",
            "quotesProposed": 4, "quotesVerified": 3})
        stats = await repo.count_recent_quote_stats(session, INST)
    assert empty["hitRate"] is None
    assert stats == {"proposed": 4, "verified": 3, "hitRate": 0.75}
