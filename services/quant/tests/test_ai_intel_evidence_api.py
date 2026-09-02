"""证据接地的两个新端点与三个新设置键（§7.1 · test_ai_intel_evidence_api）。

全程离线（不出网、不起会话）。G-15 / G-17 / C-8 在这里。

`test_documents_health_is_not_hijacked_by_read_intel` 钉的是一条**注册顺序**：FastAPI 按注册
顺序匹配，字面量路径排在 `/intel/{instId}` 之后会被静默劫持（`instId="documents"`），
症状是一次 500 而不是 404。
"""
from __future__ import annotations

import time

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.ai_auto.intel import runtime as intel_runtime
from app.ai_auto.intel import service as intel_service
from app.config import get_settings
from app.db import repositories as repo
from app.db.models import (
    AiIntelDocument,
    AiIntelItem,
    AiIntelQuote,
    AiIntelRound,
    AiIntelWatch,
    AppSetting,
)
from app.db.session import get_sessionmaker, init_db

INST = "BTC-USDT-SWAP"
NOW = int(time.time())
_ORIGIN = {"Origin": "http://localhost:18761"}


def _client() -> AsyncClient:
    from app.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers=_ORIGIN)


async def _fresh() -> None:
    await init_db()
    async with get_sessionmaker()() as session:
        for table in (AiIntelQuote, AiIntelDocument, AiIntelItem, AiIntelRound, AiIntelWatch):
            await session.execute(delete(table))
        await session.execute(delete(AppSetting))
        await session.commit()
        await repo.set_workspace_watchlist(session, watchlist=[INST], members=[],
                                           allocation_mode="even", symbol_groups=[])
    intel_runtime._RUNTIMES.clear()
    intel_runtime._BY_INST.clear()


async def _seed_item(*, with_quote: bool = True, with_doc: bool = True) -> int:
    async with get_sessionmaker()() as session:
        ids = await repo.upsert_intel_items(session, [{
            "instId": INST, "tier": "ultra", "category": "macro", "headline": "核心 CPI 3.1%",
            "summary": "核心 CPI 同比 3.1%，高于市场预期。", "stance": "bearish",
            "impact": "high", "confidence": 0.7, "eventTs": NOW, "capturedTs": NOW,
            "expiresTs": NOW + 3600, "status": "active", "origin": "agent",
            "dedupKey": "ev-api-1", "domainCount": 1, "sources": [
                {"url": "https://reuters.com/a", "title": "t", "publisher": "reuters.com",
                 "domain": "reuters.com", "publishedTs": NOW}]}])
        if with_doc:
            from app.ai_auto.intel.corpus import document_key
            await repo.upsert_documents(session, [{
                "urlHash": document_key("https://reuters.com/a"),
                "url": "https://reuters.com/a", "finalUrl": "https://reuters.com/a",
                "domain": "reuters.com", "title": "t", "body": "正文" * 20, "bodyChars": 40,
                "byteLen": 900, "status": "ok", "httpStatus": 200, "fetchedTs": NOW}])
        if with_quote:
            await repo.write_intel_quotes(session, [{
                "itemId": ids[0], "instId": INST, "roundId": 7, "role": "admit", "ref": "D1",
                "text": "核心 CPI 同比 3.1%", "sourceField": "body",
                "docUrl": "https://reuters.com/a", "docDomain": "reuters.com",
                "docPublishedTs": NOW, "charStart": 3, "hasImperative": False}])
    return ids[0]


# ---------------- N-1：证据抽屉 ----------------
@pytest.mark.asyncio
async def test_evidence_endpoint_returns_quotes_and_doc_state():
    await _fresh()
    item_id = await _seed_item()
    async with _client() as client:
        res = await client.get(f"/api/intel/items/{item_id}/evidence")
    assert res.status_code == 200
    body = res.json()
    assert body["itemId"] == item_id and body["quoteMode"] in ("off", "soft", "strict")
    assert body["quotes"][0]["text"] == "核心 CPI 同比 3.1%"
    assert body["quotes"][0]["role"] == "admit"
    assert body["docs"][0]["domain"] == "reuters.com"


@pytest.mark.asyncio
async def test_evidence_endpoint_returns_200_and_empty_for_item_without_quotes():
    """无引文 ⇒ 200 + 空数组，**不是 404**：那会让前端把一次正常展开画成一次请求失败。"""
    await _fresh()
    item_id = await _seed_item(with_quote=False, with_doc=False)
    async with _client() as client:
        res = await client.get(f"/api/intel/items/{item_id}/evidence")
    assert res.status_code == 200
    assert res.json()["quotes"] == [] and res.json()["docs"] == []


@pytest.mark.asyncio
async def test_evidence_404_for_unknown_item():
    await _fresh()
    async with _client() as client:
        res = await client.get("/api/intel/items/999999/evidence")
    assert res.status_code == 404


# ---------------- N-2：缓存健康 ----------------
@pytest.mark.asyncio
async def test_documents_health_is_not_hijacked_by_read_intel():
    """G-17：字面量路径排在 `/intel/{instId}` **之前**——否则 `instId="documents"` 会 500。"""
    await _fresh()
    await _seed_item()
    async with _client() as client:
        res = await client.get("/api/intel/documents/health")
    assert res.status_code == 200
    body = res.json()
    assert body["cachedDocs"] == 1 and body["bodyBytes"] > 0
    assert set(body) == {"cachedDocs", "bodyBytes", "oldestFetchedTs", "lastPrunedTs",
                         "readerEnabled",
                         # silent-degradation-repair 轴 B-2 的六个加法键。它们必须**真的到达
                         # 客户端**——本端点声明了 `response_model`，schema 漏改时 FastAPI 会
                         # 一声不响地把它们全部剔除（评审 P0-2，阻断级）。
                         "fetchAttempt", "fetchOk", "zeroOkStreak", "blockedByStatus",
                         "rejectCodesSinceStart", "sinceProcessStart"}


# ---------------- M-1 / M-2：出参补键（G-21 的读出通路） ----------------
@pytest.mark.asyncio
async def test_read_intel_carries_the_four_new_item_keys():
    await _fresh()
    item_id = await _seed_item()
    async with get_sessionmaker()() as session:
        await repo.write_intel_quote_state(session, {item_id: (True, 1, "")})
    async with _client() as client:
        body = (await client.get(f"/api/intel/{INST}")).json()
    item = body["tiers"]["ultra"]["items"][0]
    assert item["quoted"] is True and item["quoteCount"] == 1
    assert item["quoteState"] == "" and item["scheduleResolvedTs"] == 0


@pytest.mark.asyncio
async def test_rounds_carry_the_four_new_counters():
    await _fresh()
    async with get_sessionmaker()() as session:
        await repo.write_intel_round(session, {
            "instId": INST, "kind": "collect", "startedTs": NOW, "status": "ok",
            "readerAttempt": 6, "readerOk": 5, "quotesProposed": 9, "quotesVerified": 7})
    async with _client() as client:
        rounds = (await client.get(f"/api/intel/{INST}/rounds")).json()["rounds"]
    assert rounds[0]["readerAttempt"] == 6 and rounds[0]["readerOk"] == 5
    assert rounds[0]["quotesProposed"] == 9 and rounds[0]["quotesVerified"] == 7


# ---------------- M-3：三个新设置键 ----------------
@pytest.mark.asyncio
async def test_every_new_intel_setting_key_round_trips_through_the_bridge():
    """G-15：**逐键**往返，而不是「PUT 不报错」。

    不扩 `INTEL_SETTING_BRIDGE` ⇒ 请求 200、面板上开关照动、`Settings` 上的值永远不变，
    而且不报错——把安全开关做成假开关是最不可接受的一种失败。
    """
    await _fresh()
    patch = {"readerEnabled": False, "readerTopK": 3, "quoteMode": "off"}
    async with _client() as client:
        res = await client.put("/api/settings/intel", json=patch)
    assert res.status_code == 200
    body = res.json()
    for key, value in patch.items():
        assert body[key] == value, f"{key} 没有经 bridge 回读到生效值"

    async with get_sessionmaker()() as session:
        derived = await intel_service.resolve_intel_settings(session, get_settings())
    assert derived.ai_trade_intel_reader_enabled is False
    assert derived.ai_trade_intel_reader_top_k == 3
    assert derived.ai_trade_intel_quote_mode == "off"


@pytest.mark.asyncio
async def test_settings_rejects_unknown_quote_mode():
    await _fresh()
    async with _client() as client:
        res = await client.put("/api/settings/intel", json={"quoteMode": "paranoid"})
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_bad_quote_mode_in_the_db_never_reaches_settings():
    """C-8：`model_copy(update=…)` **不做校验**——一个坏值必须在读回处被挡下。"""
    await _fresh()
    async with get_sessionmaker()() as session:
        await repo.set_app_setting(session, "ai_intel.quote_mode", "paranoid")
        derived = await intel_service.resolve_intel_settings(session, get_settings())
    assert derived.ai_trade_intel_quote_mode == "soft", "坏值必须被跳过，回落到默认"


@pytest.mark.asyncio
async def test_reader_disabled_shows_up_in_document_health():
    await _fresh()
    async with _client() as client:
        await client.put("/api/settings/intel", json={"readerEnabled": False})
        body = (await client.get("/api/intel/documents/health")).json()
    assert body["readerEnabled"] is False


@pytest.mark.asyncio
async def test_strict_without_reader_is_not_reachable_through_the_panel():
    """`strict` + 抓取关闭这个坏组合，不能从设置面板走进来（终审补）。

    `config.py` 的启动期校验器只看得住 `.env`，而这两把单键在面板上是**分开写的两行**、各自
    独立地落进 `app_settings`——用户完全可以先选 strict 再关掉抓取。没有正文时 strict 的准入率
    **系统性归零**，而它在界面上与「今天真的没新闻」长得一模一样，正是那条校验器要拦的状态。
    生效值在 `tax.resolve_quote_mode` 这一个读点上降回 `soft`；**用户存下来的偏好不动**，
    重新打开抓取即恢复。
    """
    from app.ai_auto.intel import taxonomy as tax

    await _fresh()
    async with _client() as client:
        res = await client.put("/api/settings/intel",
                               json={"quoteMode": "strict", "readerEnabled": False})
        assert res.status_code == 200
        assert res.json()["quoteMode"] == "soft", "回读的必须是生效值，不是存下来的那个"
        back = await client.put("/api/settings/intel", json={"readerEnabled": True})
    assert back.json()["quoteMode"] == "strict", "偏好没有被抹掉，抓取一开就恢复"

    async with get_sessionmaker()() as session:
        await repo.set_app_setting(session, "ai_intel.reader_enabled", False)
        derived = await intel_service.resolve_intel_settings(session, get_settings())
    assert derived.ai_trade_intel_quote_mode == "strict", "存下来的偏好原样保留"
    limits = tax.limits_from_settings(derived)
    assert limits.quote_mode == "soft", "纯核心拿到的必须是降档后的生效值"
    assert limits.promote_requires_quote is False, "升长期的额外门槛随之整条不存在"
