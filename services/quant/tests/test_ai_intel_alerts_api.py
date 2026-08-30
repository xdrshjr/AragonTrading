"""告警台账与两处扩返回的 REST 出参（ai-trade-intel-actionability G-11 / G-23）。

**G-23 的判据是 HTTP 响应体，不是仓库函数的返回值**：`GET /impact` 与 `GET /sources` 都挂着
`response_model`，而 FastAPI 会**静默丢弃**未在模型里声明的键——症状是「后端算了、前端永远
是 0」，而不是一条报错（E-19 / R-5）。只断言仓库函数的用例对这条缺陷完全不敏感。
"""
from __future__ import annotations

import time

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.db import repositories as repo
from app.db.models import AiIntelAlert, AiIntelCitation, AiIntelItem, AiIntelRound, AiIntelWatch
from app.db.session import get_sessionmaker, init_db

NOW = int(time.time())
INST = "BTC-USDT-SWAP"
_ORIGIN = {"Origin": "http://localhost:18761"}


def _client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers=_ORIGIN)


async def _fresh() -> None:
    await init_db()
    async with get_sessionmaker()() as session:
        for table in (AiIntelAlert, AiIntelCitation, AiIntelItem, AiIntelRound, AiIntelWatch):
            await session.execute(delete(table))
        await session.commit()


def _alert(**over) -> dict:
    base = {"instId": INST, "groupId": "grp-1", "itemId": 41, "roundId": 7,
            "createdTs": NOW, "severity": 0.82, "fired": True, "suppressedCode": "",
            "headline": "某交易所热钱包异常出金 1.2 万枚 ETH", "category": "risk",
            "tier": "ultra", "stance": "bearish", "impact": "high", "domainCount": 3,
            "quoted": True, "confidence": 0.78,
            "reasons": {"impactW": 1.0, "freshness": 0.91, "ageMin": 16.4}}
    return {**base, **over}


def _item(headline: str, **over) -> dict:
    base = {"instId": INST, "tier": "ultra", "category": "risk", "headline": headline,
            "summary": f"{headline}。这条摘要写得足够长以通过长度判据。", "stance": "bearish",
            "impact": "high", "confidence": 0.8, "eventTs": NOW - 7200,
            "capturedTs": NOW - 7200, "expiresTs": NOW + 86400, "status": "active",
            "pinned": False, "userEdited": False, "undated": False, "isScheduled": False,
            "origin": "agent", "originGroupId": "", "originRoundId": 0,
            "sources": [{"url": "https://coindesk.com/a", "domain": "coindesk.com"}],
            "domainCount": 1, "dedupKey": f"k-{abs(hash(headline)) % 10**8}"}
    return {**base, **over}


# ---------- G-11：台账两写点从 REST 出参闭合 ----------
@pytest.mark.asyncio
async def test_the_ledger_round_trip_reads_back_through_rest():
    """造一条 fired 的告警 → 会话侧回写 → **从 REST 输出**（不是从 DB）读到结果与轮指针。"""
    await _fresh()
    async with get_sessionmaker()() as session:
        alert_id = await repo.write_intel_alert(session, _alert())
        await repo.stamp_intel_alert_digest(session, alert_id, ref="I02", in_digest=True)
        await repo.mark_intel_alert_outcome(session, alert_id, outcome="woke",
                                            outcome_ts=NOW + 2, run_id=37, round_no=47)
    async with _client() as client:
        body = (await client.get(f"/api/intel/{INST}/alerts")).json()
    assert body["hasMore"] is False
    row = body["alerts"][0]
    assert row["outcome"] == "woke" and row["runId"] == 37 and row["roundNo"] == 47
    assert row["outcomeText"] == "已唤醒一轮决策"
    # 第三跳补写的两列同样要从 REST 读得回来：动态页签的 `[Ixx]` 前缀与「未进入情报段」
    # 各读其中一个，两者**不是同一件事**（`cite` 关时空 `ref` 是合法取值）。
    assert row["ref"] == "I02" and row["inDigest"] is True
    # ★ 本端点**不挂 `response_model`**：`reasons` 是自由形状的判据明细，挂模型只会得到
    # 「加了新判据、界面上看不到、也不报错」（评审 P2-9）。
    assert row["reasons"] == {"impactW": 1.0, "freshness": 0.91, "ageMin": 16.4}


@pytest.mark.asyncio
async def test_suppressed_rows_are_listed_too_with_a_chinese_label():
    """「今天为什么一次都没告警」的答案就住在这些行里——空轮也写一行。"""
    await _fresh()
    async with get_sessionmaker()() as session:
        await repo.write_intel_alert(session, _alert(fired=False, itemId=0, headline="",
                                                     suppressedCode="no_candidate"))
    async with _client() as client:
        body = (await client.get(f"/api/intel/{INST}/alerts")).json()
    row = body["alerts"][0]
    assert row["fired"] is False
    assert row["suppressedCode"] == "no_candidate"
    assert row["suppressedText"] == "本轮没有任何新情报"
    assert row["outcome"] == "" and row["outcomeText"] == ""
    # 未经第三跳的行：两列都在位且为「空 / 假」，前端因此永远读不到 `undefined`。
    assert row["ref"] == "" and row["inDigest"] is False


@pytest.mark.asyncio
async def test_alerts_pagination_is_keyed_by_id():
    await _fresh()
    async with get_sessionmaker()() as session:
        for _ in range(3):
            await repo.write_intel_alert(session, _alert())
    async with _client() as client:
        first = (await client.get(f"/api/intel/{INST}/alerts?limit=2")).json()
        assert first["hasMore"] is True
        rest = (await client.get(
            f"/api/intel/{INST}/alerts?limit=5&before={first['alerts'][-1]['id']}")).json()
    assert len(rest["alerts"]) == 1 and rest["hasMore"] is False


# ---------- G-23：`response_model` 没吞掉新键 ----------
@pytest.mark.asyncio
async def test_impact_response_body_carries_the_efficacy_block():
    await _fresh()
    async with get_sessionmaker()() as session:
        ids = await repo.upsert_intel_items(session, [_item(f"风险事件 {i}") for i in range(6)])
        for item_id in ids:
            row = await session.get(AiIntelItem, item_id)
            row.verdict = "confirmed"
        await session.commit()
    async with _client() as client:
        body = (await client.get(f"/api/intel/{INST}/impact")).json()
    assert "efficacy" in body, "response_model 静默吞掉了新键（E-19 / R-5）"
    efficacy = body["efficacy"]
    assert efficacy["enabled"] is True
    assert efficacy["sampleN"] == 6
    assert efficacy["overall"]["confirmed"] == 6
    assert efficacy["overall"]["accuracy"] == 1.0
    row = next(r for r in efficacy["byCategory"] if r["category"] == "risk")
    assert row["confirmed"] == 6 and isinstance(row["weight"], float)


@pytest.mark.asyncio
async def test_sources_response_body_carries_the_reputation_columns():
    """`accuracy` **可为 `null`**（样本不足）——绝不显示一个用两条样本算出来的 100%。"""
    await _fresh()
    async with get_sessionmaker()() as session:
        ids = await repo.upsert_intel_items(session, [_item(f"风险事件 {i}") for i in range(2)])
        for item_id in ids:
            row = await session.get(AiIntelItem, item_id)
            row.verdict = "refuted"
        await session.commit()
    async with _client() as client:
        body = (await client.get(f"/api/intel/{INST}/sources")).json()
    row = next(r for r in body["rows"] if r["domain"] == "coindesk.com")
    for key in ("confirmed", "refuted", "unresolved", "accuracy"):
        assert key in row, "response_model 静默吞掉了信誉列"
    assert row["refuted"] == 2
    assert row["accuracy"] is None, "样本 2 < efficacy_min_domain_samples(3) ⇒ 样本不足"


@pytest.mark.asyncio
async def test_the_read_endpoint_carries_the_conflicts_key():
    await _fresh()
    topic = "ETF 资金流向出现明显变化机构仓位随之调整"
    async with get_sessionmaker()() as session:
        await repo.upsert_intel_items(session, [
            _item(f"{topic}，连续第 5 日净流入", stance="bullish", category="market"),
            _item(f"{topic}，单日净流出创年内新高", stance="bearish", category="market")])
        await session.commit()
    async with _client() as client:
        body = (await client.get(f"/api/intel/{INST}")).json()
    assert len(body["conflicts"]) == 1
    pair = body["conflicts"][0]
    assert pair["aId"] < pair["bId"] and pair["category"] == "market"
    assert 0.0 < pair["overlap"] <= 1.0 and pair["openRounds"] >= 0


@pytest.mark.asyncio
async def test_reading_alerts_needs_no_master_switch():
    """看自己的告警历史不动任何资金（与 `GET /intel/{instId}` 同一条纪律）。"""
    await _fresh()
    async with _client() as client:
        res = await client.get("/api/intel/NEVER-SEEN-SWAP/alerts")
    assert res.status_code == 200
    assert res.json() == {"alerts": [], "hasMore": False}


@pytest.mark.asyncio
async def test_no_alert_row_ever_leaks_a_url_or_a_domain():
    """wire 上**绝不出现** URL / 域名 / 原文正文 / 检索源 key（§3.9 的提示注入防护姿态）。"""
    await _fresh()
    async with get_sessionmaker()() as session:
        await repo.write_intel_alert(session, _alert())
    async with _client() as client:
        body = (await client.get(f"/api/intel/{INST}/alerts")).json()
    text = str(body)
    assert "http" not in text and "coindesk" not in text
    assert (await _alert_count()) == 1


async def _alert_count() -> int:
    async with get_sessionmaker()() as session:
        return len((await session.execute(select(AiIntelAlert.id))).all())
