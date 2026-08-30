"""信源治理的四个端点（closed-loop §7.1 · test_ai_intel_feedback_api）。

G-9 的三条在这里：①拉黑之后语料过滤生效（`corpus.build` 侧另有用例）；②**存量扫除**只扫
「全部来源都被拉黑」的条目；③解除拉黑**不复活**任何条目。

全程离线（不出网、不起会话）。
"""
from __future__ import annotations

import time

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.ai_auto.intel import runtime as intel_runtime
from app.db import repositories as repo
from app.db.models import (
    AiIntelCitation,
    AiIntelFeedback,
    AiIntelItem,
    AiIntelRound,
    AiIntelWatch,
    AppSetting,
)
from app.db.session import dispose_engine, get_sessionmaker, init_db

INST = "BTC-USDT-SWAP"
NOW = int(time.time())
_ORIGIN = {"Origin": "http://localhost:18761"}
DIRTY = "coinmarketwatch.example"
CLEAN = "coindesk.com"


def _client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers=_ORIGIN)


async def _fresh() -> None:
    await init_db()
    async with get_sessionmaker()() as session:
        for table in (AiIntelCitation, AiIntelItem, AiIntelRound, AiIntelWatch,
                      AiIntelFeedback):
            await session.execute(delete(table))
        await session.execute(delete(AppSetting))
        await session.commit()
        await repo.set_workspace_watchlist(session, watchlist=[INST], members=[],
                                           allocation_mode="even", symbol_groups=[])
    intel_runtime._RUNTIMES.clear()
    intel_runtime._BY_INST.clear()


def _payload(dedup: str, domains: list[str], **over) -> dict:
    body = {"instId": INST, "tier": "ultra", "category": "market", "headline": f"h-{dedup}",
            "summary": "s", "stance": "neutral", "impact": "medium", "confidence": 0.5,
            "eventTs": NOW - 3600, "capturedTs": NOW - 3600, "expiresTs": NOW + 86400,
            "status": "active", "origin": "agent", "dedupKey": dedup,
            "sources": [{"url": f"https://{d}/x", "domain": d, "publishedTs": 0}
                        for d in domains],
            "domainCount": len(set(domains))}
    body.update(over)
    return body


async def _seed() -> dict[str, int]:
    async with get_sessionmaker()() as session:
        ids = await repo.upsert_intel_items(session, [
            _payload("all-dirty", [DIRTY]),
            _payload("mixed", [DIRTY, CLEAN]),
            _payload("protected", [DIRTY], pinned=True),
        ])
    return {"dirty": ids[0], "mixed": ids[1], "protected": ids[2]}


async def _status(item_id: int) -> str:
    async with get_sessionmaker()() as session:
        return (await repo.get_intel_item(session, item_id))["status"]


@pytest.mark.asyncio
async def test_preview_writes_nothing():
    """#15b：确认框必须显示受影响条数，而算它的那一次**绝不写库**。"""
    await _fresh()
    ids = await _seed()
    async with _client() as client:
        res = await client.post("/api/intel/feedback/preview",
                                json={"key": DIRTY, "action": "block", "instId": INST})
        assert res.status_code == 200
        assert res.json()["affected"] == 1, "只算「全部来源都被拉黑」且未受保护的那一条"

        listed = await client.get("/api/intel/feedback")
    assert listed.json()["rows"] == [], "preview 之后黑白名单必须仍是空的"
    assert await _status(ids["dirty"]) == "active"
    await dispose_engine()


@pytest.mark.asyncio
async def test_preview_for_trust_is_always_zero():
    """`trust` 不归档任何东西——给它算一个非零的「受影响条数」会吓退一个安全动作。"""
    await _fresh()
    await _seed()
    async with _client() as client:
        res = await client.post("/api/intel/feedback/preview",
                                json={"key": DIRTY, "action": "trust", "instId": INST})
    assert res.json()["affected"] == 0
    await dispose_engine()


@pytest.mark.asyncio
async def test_block_archives_items_whose_every_source_is_blocked():
    """G-9②：全部来源被拉黑 ⇒ 归档；只要还有一个干净来源就留着；受保护条目跳过。"""
    await _fresh()
    ids = await _seed()
    async with _client() as client:
        res = await client.post("/api/intel/feedback",
                                json={"key": DIRTY, "action": "block", "instId": INST})
        assert res.status_code == 200 and res.json() == {"ok": True, "archived": 1}

        rows = (await client.get(f"/api/intel/feedback?instId={INST}")).json()["rows"]
    assert [(r["key"], r["action"]) for r in rows] == [(DIRTY, "block")]
    assert await _status(ids["dirty"]) == "archived"
    assert await _status(ids["mixed"]) == "active", "还有一个干净来源就不该被扫掉"
    assert await _status(ids["protected"]) == "active", "两把免死金牌在存量扫除上同样有效"
    await dispose_engine()


@pytest.mark.asyncio
async def test_sweep_can_be_turned_off_and_then_nothing_is_archived():
    """存量扫除单键必须**真的是单键**：面板上关掉之后，拉黑只记名单、不动存量。

    这条同时钉住一件事：端点读的是 `resolve_intel_settings` 的产物（库内行叠加后的那一份），
    而不是进程 env 上的 `Settings`——读错来源就是评审 P0-5 那类「面板上关得掉、系统永远照扫」
    的假开关。
    """
    await _fresh()
    ids = await _seed()
    async with _client() as client:
        saved = (await client.put("/api/settings/intel",
                                  json={"blockedSweepEnabled": False})).json()
        assert saved["blockedSweepEnabled"] is False, "G-18：写进去要读得出来"

        res = await client.post("/api/intel/feedback",
                                json={"key": DIRTY, "action": "block", "instId": INST})
    assert res.json() == {"ok": True, "archived": 0}
    assert await _status(ids["dirty"]) == "active", "单键关掉之后存量一条都不该被归档"
    await dispose_engine()


@pytest.mark.asyncio
async def test_unblock_does_not_revive():
    """G-9③：解除拉黑**不复活**任何条目——恢复路径是「已归档」页签里那颗按钮。"""
    await _fresh()
    ids = await _seed()
    async with _client() as client:
        await client.post("/api/intel/feedback",
                          json={"key": DIRTY, "action": "block", "instId": INST})
        res = await client.delete(
            f"/api/intel/feedback?scope=domain&key={DIRTY}&instId={INST}")
        assert res.status_code == 204
        rows = (await client.get("/api/intel/feedback")).json()["rows"]

    assert rows == []
    assert await _status(ids["dirty"]) == "archived", "自动复活会让条目数在两次点击之间跳来跳去"
    await dispose_engine()


@pytest.mark.asyncio
async def test_trust_is_recorded_and_readable():
    """P1-7：`trust` 必须真的有读点，否则它就是第二个「有写点没读点」。"""
    await _fresh()
    async with _client() as client:
        await client.post("/api/intel/feedback",
                          json={"key": CLEAN, "action": "trust", "instId": ""})
    async with get_sessionmaker()() as session:
        assert await repo.list_intel_trusted_domains(session, INST) == [CLEAN]
        assert await repo.list_intel_blocked_domains(session, INST) == []
    await dispose_engine()


@pytest.mark.asyncio
async def test_source_rows_aggregate_domains_with_their_state():
    """#17：信源页签上半的取数（域名 / 条数 / 最近一次 / 当前状态）。"""
    await _fresh()
    await _seed()
    async with _client() as client:
        await client.post("/api/intel/feedback",
                          json={"key": DIRTY, "action": "block", "instId": INST})
        rows = (await client.get(f"/api/intel/{INST}/sources")).json()["rows"]

    by_domain = {row["domain"]: row for row in rows}
    assert by_domain[DIRTY]["state"] == "blocked"
    assert by_domain[CLEAN]["state"] == ""
    assert by_domain[DIRTY]["itemCount"] >= 1
    # 取舍是按标的写的 ⇒ 作用域就是这个标的（界面上那颗「撤销」按它发才删得中）。
    assert by_domain[DIRTY]["stateInstId"] == INST
    assert by_domain[CLEAN]["stateInstId"] == ""
    await dispose_engine()


@pytest.mark.asyncio
async def test_source_row_reports_the_scope_of_a_global_block_not_the_symbol():
    """一条**全局**拉黑在按标的取的清单里也要报出自己的作用域。

    `clear_intel_feedback` 按 ``(key, inst_id)`` 精确删。不下发作用域，界面只能拿当前标的去猜，
    而猜错的后果是一次**什么都不会发生、也不报任何错**的「撤销」——正是本轮要根治的那类沉默。
    """
    await _fresh()
    await _seed()
    async with _client() as client:
        await client.post("/api/intel/feedback",
                          json={"key": DIRTY, "action": "block", "instId": ""})
        rows = (await client.get(f"/api/intel/{INST}/sources")).json()["rows"]
    row = {r["domain"]: r for r in rows}[DIRTY]
    assert row["state"] == "blocked"
    assert row["stateInstId"] == "", "全局记录必须报空作用域，而不是报成本标的"

    # 按它报出来的作用域撤销 ⇒ 状态真的回到未表态。
    async with _client() as client:
        await client.delete(f"/api/intel/feedback?scope=domain&key={DIRTY}&instId=")
        rows = (await client.get(f"/api/intel/{INST}/sources")).json()["rows"]
    assert {r["domain"]: r for r in rows}[DIRTY]["state"] == ""
    await dispose_engine()


@pytest.mark.asyncio
async def test_bad_scope_is_rejected_instead_of_silently_ignored():
    await _fresh()
    async with _client() as client:
        res = await client.delete("/api/intel/feedback?scope=url&key=x")
    assert res.status_code == 422
    await dispose_engine()
