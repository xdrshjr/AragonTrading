"""告警台账的两个写点、冷却/日配额现算与保留期裁剪（G-11 数据层 / G-12 / R-9）。

真 SQLite（conftest 的临时库）。这一层要守住的是**「一个事实由两个进程位置写同一行」**——
本仓库反复出错的形状：创建由采集轮写、回写由会话侧写，两者必须各自唯一、且不互相覆盖。
"""
from __future__ import annotations

import time

import pytest
from sqlalchemy import delete, select

from app.db import repositories as repo
from app.db.models import AiIntelAlert, AiIntelWatch
from app.db.session import get_sessionmaker, init_db

INST = "BTC-USDT-SWAP"
NOW = float(int(time.time()))


async def _fresh() -> None:
    await init_db()
    async with get_sessionmaker()() as session:
        await session.execute(delete(AiIntelAlert))
        await session.execute(delete(AiIntelWatch))
        await session.commit()


def _payload(**over) -> dict:
    base = {"instId": INST, "groupId": "grp-1", "itemId": 41, "roundId": 7,
            "createdTs": int(NOW), "severity": 0.82, "fired": True,
            "suppressedCode": "", "headline": "某交易所热钱包异常出金",
            "category": "risk", "tier": "ultra", "stance": "bearish", "impact": "high",
            "domainCount": 3, "quoted": True, "confidence": 0.78,
            "reasons": {"impactW": 1.0, "ageMin": 16.4}}
    return {**base, **over}


@pytest.mark.asyncio
async def test_a_new_row_never_prefills_an_outcome():
    """`outcome` 在创建时**恒为空串**：预填一个「尚未发生」的结果会让「等待响应」这个状态
    与「已经回答过了」无法区分。"""
    await _fresh()
    async with get_sessionmaker()() as session:
        alert_id = await repo.write_intel_alert(session, _payload())
        row = await session.get(AiIntelAlert, alert_id)
        assert row.outcome == "" and row.outcome_ts == 0
        assert row.run_id == 0 and row.round_no == 0


@pytest.mark.asyncio
async def test_the_second_write_point_is_idempotent():
    """一条已经结账的告警**不会**被后来的清理分支改写（`deferred` 之后再 `woke` 也不覆盖）。

    评审建议 2 的成文取舍：台账要的是「最终发生了什么」，故动态页签的文案不得写成
    「本次未被推迟过」——它说不出这句话。
    """
    await _fresh()
    async with get_sessionmaker()() as session:
        alert_id = await repo.write_intel_alert(session, _payload())
        assert await repo.mark_intel_alert_outcome(
            session, alert_id, outcome="deferred") is True
        assert await repo.mark_intel_alert_outcome(
            session, alert_id, outcome="woke", run_id=37, round_no=47) is False
        row = await session.get(AiIntelAlert, alert_id)
        assert row.outcome == "deferred" and row.run_id == 0
    async with get_sessionmaker()() as session:
        assert await repo.mark_intel_alert_outcome(session, 9999, outcome="woke") is False


@pytest.mark.asyncio
async def test_the_digest_stamp_is_its_own_write_point_and_never_touches_the_outcome():
    """`ref_token` / `in_digest` 由第三跳补写，且**只碰这两列**。

    它们在建行那一刻还不知道（digest 要等 `IntelRuntime.refresh` 重算完才有编号，而那发生在
    采集轮提交之后）。没有这个写点，两列就是「有列、有读点、没有写点」——`GET /alerts` 上
    `ref` 恒为空、`inDigest` 恒为假，动态页签于是给每一条告警都挂一句「未进入情报段」。
    """
    await _fresh()
    async with get_sessionmaker()() as session:
        alert_id = await repo.write_intel_alert(session, _payload())
        assert (await session.get(AiIntelAlert, alert_id)).ref_token == ""
        assert await repo.stamp_intel_alert_digest(session, alert_id, ref="I02",
                                                   in_digest=True)
        await repo.mark_intel_alert_outcome(session, alert_id, outcome="woke", run_id=3,
                                            round_no=9)
        wire = repo.intel_alert_out(await session.get(AiIntelAlert, alert_id))
    assert wire["ref"] == "I02" and wire["inDigest"] is True
    # 三个写点各写各的列 ⇒ 谁都没有覆盖谁。
    assert wire["outcome"] == "woke" and wire["roundNo"] == 9


@pytest.mark.asyncio
async def test_a_stamp_with_no_number_still_records_that_it_reached_the_digest():
    """`cite` 关时全库都没有编号，条目却**照样在情报段里**。

    空 `ref` 是一个合法取值，不是「没写」——前端那句「未进入情报段」只许读 `inDigest`，
    用 `!ref` 冒充它就是把后端 P0-3 那次混淆搬到界面上。
    """
    await _fresh()
    async with get_sessionmaker()() as session:
        alert_id = await repo.write_intel_alert(session, _payload())
        await repo.stamp_intel_alert_digest(session, alert_id, ref="", in_digest=True)
        wire = repo.intel_alert_out(await session.get(AiIntelAlert, alert_id))
    assert wire["ref"] == "" and wire["inDigest"] is True


@pytest.mark.asyncio
async def test_woke_carries_the_round_pointer_into_the_wire():
    await _fresh()
    async with get_sessionmaker()() as session:
        alert_id = await repo.write_intel_alert(session, _payload())
        await repo.mark_intel_alert_outcome(session, alert_id, outcome="woke",
                                            outcome_ts=int(NOW) + 2, run_id=37, round_no=47)
        rows, has_more = await repo.list_intel_alerts(session, inst_id=INST)
    assert has_more is False and len(rows) == 1
    row = rows[0]
    assert (row["outcome"], row["runId"], row["roundNo"]) == ("woke", 37, 47)
    # 文案由后端下发：前端一个字都不编（跟随 `degrade_wire` 的既有裁决）。
    assert row["outcomeText"] == "已唤醒一轮决策"
    assert row["reasons"]["impactW"] == 1.0
    # **绝不出现在 wire 上的东西**：URL、域名、正文。
    assert not any(key in row for key in ("url", "domain", "body", "summary"))


@pytest.mark.asyncio
async def test_suppressed_rows_carry_a_backend_supplied_chinese_label():
    await _fresh()
    async with get_sessionmaker()() as session:
        await repo.write_intel_alert(session, _payload(fired=False,
                                                       suppressedCode="daily_cap"))
        rows, _ = await repo.list_intel_alerts(session, inst_id=INST)
    assert rows[0]["suppressedText"] == "今日告警额度已用尽"


# ---------- G-12：冷却与日配额**从台账现算**，`ai_intel_watch` 一列都不碰 ----------
@pytest.mark.asyncio
async def test_cooldown_state_is_computed_from_the_ledger_and_never_touches_watch():
    await _fresh()
    async with get_sessionmaker()() as session:
        await repo.ensure_intel_watch(session, INST)
        before = await repo.load_intel_watch(session, INST)
        await repo.write_intel_alert(session, _payload(createdTs=int(NOW) - 30))
        await repo.write_intel_alert(session, _payload(createdTs=int(NOW) - 10))
        # 未 fired 的行**不进**任何一个计数（它们只回答「今天为什么没告警」）。
        await repo.write_intel_alert(session, _payload(fired=False,
                                                       suppressedCode="few_domains"))
        state = await repo.alert_cooldown_state(session, INST, now=NOW)
        after = await repo.load_intel_watch(session, INST)
    assert state == {"lastFiredTs": int(NOW) - 10, "dayFired": 2}
    assert before == after, "冷却与日配额不落 watch 表：没有第二份需要维护的账"


@pytest.mark.asyncio
async def test_yesterdays_alerts_do_not_count_against_todays_cap():
    await _fresh()
    async with get_sessionmaker()() as session:
        await repo.write_intel_alert(session, _payload(createdTs=int(NOW) - 3 * 86400))
        state = await repo.alert_cooldown_state(session, INST, now=NOW)
    assert state["dayFired"] == 0
    assert state["lastFiredTs"] == int(NOW) - 3 * 86400


@pytest.mark.asyncio
async def test_recent_alert_counts_distinguish_never_from_long_ago():
    """`lastAlertTs` 为 `None` = **从未告警过**，与 0 分开（后者会被渲染成 1970 年）。"""
    await _fresh()
    async with get_sessionmaker()() as session:
        empty = await repo.count_recent_alerts(session, INST, since_ts=int(NOW) - 86400)
        assert empty == {"alerts24h": 0, "lastAlertTs": None}
        await repo.write_intel_alert(session, _payload(createdTs=int(NOW) - 3600))
        await repo.write_intel_alert(session, _payload(createdTs=int(NOW) - 40 * 3600))
        counts = await repo.count_recent_alerts(session, INST, since_ts=int(NOW) - 86400)
    assert counts["alerts24h"] == 1
    assert counts["lastAlertTs"] == int(NOW) - 3600


@pytest.mark.asyncio
async def test_retention_prune_drops_only_old_rows():
    await _fresh()
    async with get_sessionmaker()() as session:
        await repo.write_intel_alert(session, _payload(createdTs=int(NOW) - 40 * 86400))
        keep = await repo.write_intel_alert(session, _payload(createdTs=int(NOW)))
        pruned = await repo.prune_intel_alerts(session, before_ts=int(NOW) - 30 * 86400)
        remaining = (await session.execute(select(AiIntelAlert.id))).scalars().all()
    assert pruned == 1
    assert remaining == [keep]


@pytest.mark.asyncio
async def test_cursor_pagination_uses_id_not_timestamp():
    """同一秒里的多行用时间戳翻页会漏行——游标必须是 `id`。"""
    await _fresh()
    async with get_sessionmaker()() as session:
        ids = [await repo.write_intel_alert(session, _payload(createdTs=int(NOW)))
               for _ in range(5)]
        first, has_more = await repo.list_intel_alerts(session, inst_id=INST, limit=2)
        assert has_more is True
        rest, _ = await repo.list_intel_alerts(session, inst_id=INST, limit=10,
                                               before=first[-1]["id"])
    assert [row["id"] for row in first] == sorted(ids, reverse=True)[:2]
    assert [row["id"] for row in rest] == sorted(ids, reverse=True)[2:]
