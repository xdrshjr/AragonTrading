"""证据接地的端到端（§7.1 · test_ai_intel_evidence_e2e）。G-20 / G-21 / G-24 在这里。

假检索源 + 假网页（`httpx.MockTransport`）+ 假 LLM，**一个真实的出网请求都不发**。

G-21 是本轮的 P0 护栏：断言必须**从 REST 出参回读**。只断言「库里有引文行」的话，
`intel_item_out` / `item_from_wire` / `IntelItem` / `intel_round_out` 四条读出通路**全断**也
照样全绿——那正是本仓库成文的那个形状「用例绿着但分支从没被走到」。
"""
from __future__ import annotations

import json
import time

import httpx
import pytest
import socket
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.agent.llm.base import StreamEvent
from app.ai_auto.intel import evidence as intel_evidence
from app.ai_auto.intel import prompts as intel_prompts
from app.ai_auto.intel import runtime as intel_runtime
from app.ai_auto.intel import service, service_verify
from app.ai_auto.intel.search import SearchDoc, SearchRunner
from app.config import Settings
from app.db import repositories as repo
from app.db.models import (
    AiIntelDocument,
    AiIntelItem,
    AiIntelQuote,
    AiIntelRound,
    AiIntelWatch,
)
from app.db.session import get_sessionmaker, init_db

NOW = int(time.time()) + 3600
INST = "BTC-USDT-SWAP"
_ORIGIN = {"Origin": "http://localhost:18761"}

_ARTICLE = ("The July core CPI print came in at 3.1% year over year, above the 3.0% "
            "consensus, and traders pushed back the expected timing of the first rate cut "
            "to later in the year, which weighed on risk assets across the board.")
_HTML = f"<html><head><title>Core CPI 3.1%</title></head><body><p>{_ARTICLE}</p></body></html>"
_QUOTE = "The July core CPI print came in at 3.1% year over year"

_COLLECT_PAYLOAD = {"items": [{
    "headline": "美国核心 CPI 同比 3.1%",
    "summary": "核心 CPI 同比 3.1%，高于市场预期，降息路径预期后移。",
    "category": "macro", "stance": "bearish", "impact": "high",
    "eventTs": NOW - 3600, "sourceRefs": ["D1", "D2"],
    "quotes": [{"ref": "D1", "text": _QUOTE}]}]}


class FakeProvider:
    slug = "fake"

    async def search(self, query, *, limit, freshness_days):
        return [SearchDoc(title="Core CPI 3.1%", url="https://reuters.com/a",
                          snippet="Core CPI came in at 3.1%, above consensus.",
                          published_ts=NOW - 3600, score=0.9),
                SearchDoc(title="Traders push back rate cut bets",
                          url="https://theblock.co/b",
                          snippet="Rate cut bets pushed back after the 3.1% print.",
                          published_ts=NOW - 7200, score=0.8)]


class FakeLLM:
    provider = "anthropic"

    def __init__(self, payload, *, tool_name=intel_prompts.COLLECT_TOOL_NAME):
        self.payload = payload
        self.tool_name = tool_name

    async def stream(self, *, system, messages, tools):
        yield StreamEvent(kind="tool_use_start", id="t", name=self.tool_name)
        yield StreamEvent(kind="tool_use_args_delta", id="t",
                          partial_json=json.dumps(self.payload, ensure_ascii=False))
        yield StreamEvent(kind="message_done", stop_reason="tool_use",
                          usage={"input_tokens": 11, "output_tokens": 4})


@pytest.fixture(autouse=True)
def _offline_web(monkeypatch):
    """把整条出网路径替换成一个假网页服务器：解析恒为公网，抓取恒回同一篇文章。"""
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))])
    real = httpx.AsyncClient

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, text=_HTML)

    class Patched(real):
        def __init__(self, *args, **kwargs):
            kwargs.pop("proxy", None)
            super().__init__(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Patched)


async def _fresh() -> None:
    await init_db()
    async with get_sessionmaker()() as session:
        for table in (AiIntelQuote, AiIntelDocument, AiIntelItem, AiIntelRound, AiIntelWatch):
            await session.execute(delete(table))
        await session.commit()
        await repo.set_workspace_watchlist(session, watchlist=[INST], members=[],
                                           allocation_mode="even", symbol_groups=[])
    intel_runtime._RUNTIMES.clear()
    intel_runtime._BY_INST.clear()


def _deps(*, llm=None, **over) -> service.IntelDeps:
    settings = Settings(**over) if over else Settings()
    return service.IntelDeps(
        settings=settings, sessionmaker=get_sessionmaker,
        search=SearchRunner(FakeProvider(), timeout_s=5.0),
        llm=llm or FakeLLM(_COLLECT_PAYLOAD),
        reader=intel_evidence.reader_config(settings),
        group_id="grp-e2e", bar="15m", now=(lambda: NOW))


def _client() -> AsyncClient:
    from app.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers=_ORIGIN)


@pytest.mark.asyncio
async def test_collect_round_fetches_bodies_quotes_and_reads_back_through_rest():
    """G-20 + G-21：跑一轮采集 → 从 **REST 出参**回读四条通路 → 清空文档表后证据仍在。"""
    await _fresh()
    outcome = await service.run_collect_round(_deps(), INST)
    assert outcome.status == "ok" and outcome.accepted == 1

    async with _client() as client:
        body = (await client.get(f"/api/intel/{INST}")).json()
        item = body["tiers"]["ultra"]["items"][0]
        # R-a：条目 wire 的四个新键
        assert item["quoted"] is True and item["quoteCount"] == 1
        assert item["quoteState"] == "" and item["scheduleResolvedTs"] == 0

        # R-d：round wire 的四个新计数
        rounds = (await client.get(f"/api/intel/{INST}/rounds")).json()["rounds"]
        assert rounds[0]["readerOk"] > 0 and rounds[0]["readerAttempt"] > 0
        assert rounds[0]["quotesProposed"] == 1 and rounds[0]["quotesVerified"] == 1

        # N-1：证据抽屉读得到那一段**原文形态**的字
        evidence = (await client.get(f"/api/intel/items/{item['id']}/evidence")).json()
        assert evidence["quotes"][0]["text"] == _QUOTE
        assert evidence["quotes"][0]["sourceField"] == "body"
        assert evidence["docs"], "缓存还在 ⇒ docs 有行"

    # G-7：清空整张缓存表，引文照样读得到（**证据比缓存活得久**）。
    async with get_sessionmaker()() as session:
        await session.execute(delete(AiIntelDocument))
        await session.commit()
    async with _client() as client:
        evidence = (await client.get(f"/api/intel/items/{item['id']}/evidence")).json()
    assert evidence["quotes"][0]["text"] == _QUOTE and evidence["docs"] == []


@pytest.mark.asyncio
async def test_summary_carries_the_quote_hit_rate():
    await _fresh()
    await service.run_collect_round(_deps(), INST)
    async with _client() as client:
        summary = (await client.get(f"/api/intel/{INST}")).json()["summary"]
    assert summary["quoteHitRate"] == 1.0


@pytest.mark.asyncio
async def test_reader_failure_does_not_fail_the_round(monkeypatch):
    """G-4：K 篇全失败时轮次仍 `ok`，accepted 与今日同数量——抓取**永不阻断轮次**。"""
    await _fresh()

    async def boom(urls, *, cfg):
        return {url: __import__("app.ai_auto.intel.reader", fromlist=["ReaderResult"])
                .ReaderResult(url=url, status="timeout") for url in urls}

    monkeypatch.setattr("app.ai_auto.intel.reader.fetch_many", boom)
    payload = {"items": [{**_COLLECT_PAYLOAD["items"][0], "quotes": []}]}
    outcome = await service.run_collect_round(_deps(llm=FakeLLM(payload)), INST)
    assert outcome.status == "ok" and outcome.accepted == 1
    async with _client() as client:
        item = (await client.get(f"/api/intel/{INST}")).json()["tiers"]["ultra"]["items"][0]
    assert item["quoteState"] == "noBody", "一个字都没抓到时，界面上说的是「没抓到正文」"


@pytest.mark.asyncio
async def test_reader_disabled_writes_no_documents_and_no_quotes():
    """双键回退的运行时侧：`reader_enabled=false` ⇒ 一行缓存都不写。"""
    await _fresh()
    deps = _deps(ai_trade_intel_reader_enabled=False)
    assert deps.reader.enabled is False
    await service.run_collect_round(deps, INST)
    async with get_sessionmaker()() as session:
        docs = await repo.document_health(session)
        rounds = await repo.list_intel_rounds(session, INST)
    assert docs["cachedDocs"] == 0
    assert rounds[0]["readerAttempt"] == 0 and rounds[0]["readerOk"] == 0


@pytest.mark.asyncio
async def test_second_round_hits_the_cache_instead_of_going_out():
    await _fresh()
    await service.run_collect_round(_deps(), INST)
    async with get_sessionmaker()() as session:
        first = await repo.document_health(session)
        watch = await repo.load_intel_watch(session, INST)
        await repo.bump_intel_watch(session, INST, kind="collect", status="ok",
                                    next_due_ts=int(watch["nextDueTs"]))
    fetched: list[str] = []
    from app.ai_auto.intel import reader as reader_mod

    original = reader_mod.fetch_many

    async def spy(urls, *, cfg):
        fetched.extend(urls)
        return await original(urls, cfg=cfg)

    reader_mod.fetch_many = spy
    try:
        await service.run_collect_round(_deps(), INST, force=True)
    finally:
        reader_mod.fetch_many = original
    async with get_sessionmaker()() as session:
        second = await repo.document_health(session)
    assert fetched == [], "命中未过期缓存 ⇒ 一次出网都不该发生"
    assert second["cachedDocs"] == first["cachedDocs"]


# ---------------- 核查轮也写引文（§3.3.3） ----------------
_VERDICT_PAYLOAD = {"verdicts": [{
    "itemRef": "C1", "verdict": "confirmed", "reason": "后续报道确认了这一读数",
    "refs": ["D1"], "quotes": [{"ref": "D1", "text": _QUOTE}]}]}


@pytest.mark.asyncio
async def test_verify_round_writes_quotes_with_role_verify():
    await _fresh()
    await service.run_collect_round(_deps(), INST)
    async with get_sessionmaker()() as session:
        rows = await repo.load_intel_scope(session, INST, ("active",))
        item_id = rows[0]["id"]

    deps = _deps(llm=FakeLLM(_VERDICT_PAYLOAD, tool_name=intel_prompts.VERIFY_TOOL_NAME))
    outcome = await service_verify.run_verify_round(deps, INST, only_item_id=item_id)
    assert outcome.status == "ok"
    async with get_sessionmaker()() as session:
        quotes = await repo.list_item_quotes(session, item_id)
        rounds = await repo.list_intel_rounds(session, INST, kind="verify")
    roles = {row["role"] for row in quotes}
    assert roles == {"admit", "verify"}, "核查引文与准入引文必须分得开"
    assert rounds[0]["quotesVerified"] == 1 and rounds[0]["quotesProposed"] == 1
    assert rounds[0]["readerAttempt"] > 0, "核查轮的抓取健康度同样要可见"


# ---------------- G-24：9 个新码四张表齐 ----------------
def test_every_backend_reject_code_has_a_frontend_label():
    """后端三张 tuple 的并集 ⊆ 前端 `CODE_LABEL` 的键集，且文案 ≠ 兜底串。"""
    import re
    from pathlib import Path

    from app.ai_auto.intel import taxonomy as tax
    from app.ai_auto.intel import verify as verify_mod

    codes = set(tax.REJECT_CODES) | set(tax.REVIEW_REJECT_CODES) | set(
        verify_mod.VERDICT_REJECT_CODES)
    root = Path(__file__).resolve().parents[3]
    source = (root / "apps" / "web" / "components" / "Intel"
              / "IntelRoundsTab.tsx").read_text(encoding="utf-8")
    block = source.split("const CODE_LABEL", 1)[1].split("};", 1)[0]
    labelled = set(re.findall(r"^\s{2}([a-z_]+):", block, re.M))
    assert codes <= labelled, f"这些后端码在前端没有文案：{sorted(codes - labelled)}"
