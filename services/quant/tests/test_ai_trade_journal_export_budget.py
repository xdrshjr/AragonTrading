"""导出的墙钟预算与分片契约（ai-trade-audit-journal §2.5.1 加强一 / 评审 RP0-2）。

流式解决的是内存，解决不了墙钟。`EXPORT_MAX_ROWS` 是**行数**闸，按 §2.3 的量级它在触顶之前
早就撞上 Next 代理那堵 300 秒的墙了。本文件断的是那道**墙钟**闸：触顶即干净收尾、`eof` 如实
说自己不完整、并给出下一片的游标；两片之间**无重、无漏、并集 = 全集**。

⚠️ 触发截断用的是一个**极小的正数**预算，不是 `0`：spec §七 第 16 项把 `0` 定义成「该闸不
生效」（无代理直连排障用），而 §8.1 的用例描述写的是「预算设成 0.0 ⇒ 首页之后即收尾」——
两处互相矛盾。这里按 §七 的语义实现与断言（`0` = 关闸，另有一条用例专门守它），
因为那是**设置项文档与 `.env.example` 双向登记的那一份**。
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import delete

from app.ai_auto.journal.export_sft import (
    SftContext,
    SftOptions,
    preview,
    slice_cursors,
)
from app.db import ai_journal_repositories as journal_repo
from app.ai_auto.journal.export_audit import (
    ROW_SEP,
    TRUNCATED_MARKER,
    ExportBudget,
    stream_audit_csv,
    stream_audit_jsonl,
)
from app.db.models import AiTradeDecisionLog, AiTradeEventLog, AiTradePromptProfile
from app.db.session import get_sessionmaker, init_db

from .test_ai_trade_journal_api import GROUP, decision_row

TOTAL = 6


async def _fresh() -> None:
    await init_db()
    async with get_sessionmaker()() as session:
        for table in (AiTradeDecisionLog, AiTradeEventLog, AiTradePromptProfile):
            await session.execute(delete(table))
        for index in range(1, TOTAL + 1):
            session.add(decision_row(index))
        await session.commit()


async def _lines(stream) -> list[dict]:
    text = "".join([chunk async for chunk in stream])
    return [json.loads(line) for line in text.splitlines() if line]


@pytest.mark.asyncio
async def test_zero_time_budget_stops_after_the_first_page_and_says_why():
    await _fresh()
    budget = ExportBudget(page_size=2, max_rows=1000, time_budget_s=1e-9)
    lines = await _lines(stream_audit_jsonl({"groupId": GROUP}, budget, {}))
    eof = lines[-1]
    assert eof["kind"] == "eof"
    assert eof["complete"] is False
    assert eof["truncatedReason"] == "time_budget"
    assert eof["nextBeforeId"], "截断必须给出下一片的游标——它就是分片契约"


@pytest.mark.asyncio
async def test_two_slices_have_no_overlap_no_gap_and_cover_everything():
    """按 `eof.nextBeforeId` 再取一次：两片的并集必须**恰好**是全集。"""
    await _fresh()
    budget = ExportBudget(page_size=3, max_rows=3, time_budget_s=0.0)
    first = await _lines(stream_audit_jsonl({"groupId": GROUP}, budget, {}))
    first_ids = [row["id"] for row in first if row["kind"] == "decision"]
    cursor = first[-1]["nextBeforeId"]
    assert first[-1]["complete"] is False and cursor

    wide = ExportBudget(page_size=100, max_rows=1000, time_budget_s=0.0)
    second = await _lines(stream_audit_jsonl(
        {"groupId": GROUP, "beforeId": cursor}, wide, {}))
    second_ids = [row["id"] for row in second if row["kind"] == "decision"]

    assert set(first_ids) & set(second_ids) == set(), "两片之间不得重叠"
    assert len(first_ids) + len(second_ids) == TOTAL, "两片的并集必须是全集"


@pytest.mark.asyncio
async def test_max_rows_reports_its_own_reason():
    await _fresh()
    budget = ExportBudget(page_size=10, max_rows=2, time_budget_s=0.0)
    lines = await _lines(stream_audit_jsonl({"groupId": GROUP}, budget, {}))
    assert lines[-1]["truncatedReason"] == "max_rows"


@pytest.mark.asyncio
async def test_csv_truncation_marker_carries_the_same_cursor():
    """CSV 没有 eof 行可写，故截断时追加一行 `#TRUNCATED,<reason>,<nextBeforeId>`。"""
    await _fresh()
    budget = ExportBudget(page_size=2, max_rows=2, time_budget_s=0.0)
    text = "".join([chunk async for chunk in stream_audit_csv({"groupId": GROUP}, budget)])
    last = [line for line in text.split(ROW_SEP) if line][-1]
    marker, reason, cursor = last.split(",")
    assert marker == TRUNCATED_MARKER and reason == "max_rows" and int(cursor) > 0


@pytest.mark.asyncio
async def test_no_truncation_means_complete_true_and_no_cursor():
    await _fresh()
    lines = await _lines(stream_audit_jsonl({"groupId": GROUP}, ExportBudget(), {}))
    eof = lines[-1]
    assert eof["complete"] is True
    assert eof["truncatedReason"] == "none"
    assert eof["nextBeforeId"] is None, "没截断就不该给游标——那会让前端多下一个空片"


def test_budget_zero_disables_the_wall_clock_gate():
    """`EXPORT_TIME_BUDGET_S=0` ⇒ 该闸不生效（**只在无代理的直连排障场景下这么用**）。"""
    budget = ExportBudget(max_rows=0, time_budget_s=0.0)
    assert budget.exhausted(started=0.0, emitted=10_000) is None


def test_slice_cursors_split_the_scanned_ids_exactly():
    """分片游标是**精确**的：第 k 片的边界就是第 k 段最后一行的 id。

    没有它，分片就是假的——浏览器导航式的下载读不回 `eof.nextBeforeId`，前端只能一片片从头
    拉，最后得到 N 个内容重叠的文件，而它在界面上和一次正常分片长得一模一样。
    """
    ids = [100, 99, 98, 97, 96, 95]          # 导出顺序恒为 id DESC
    assert slice_cursors(ids, 1) == []
    assert slice_cursors(ids, 2) == [98]     # 第 1 片到 98 为止，第 2 片从 < 98 起
    assert slice_cursors(ids, 3) == [99, 97]
    # 行数不够切时只给切得出来的那几个（调用方据此收敛片数，绝不发一片没有游标的请求）。
    assert slice_cursors([100], 3) == []
    assert slice_cursors([], 3) == []


@pytest.mark.asyncio
async def test_sft_preview_hands_back_usable_slice_cursors():
    await _fresh()
    ctx = SftContext(context_chain_enabled=True, resolved_min_link_confidence="none",
                     resolved_reason="explicit")
    budget = ExportBudget(page_size=100, max_rows=1000, time_budget_s=0.0)
    result = await preview({"groupId": GROUP}, budget, SftOptions(only_settled=False,
                                                                 include_hold=True), ctx)
    assert "sliceCursors" in result
    assert len(result["sliceCursors"]) <= max(0, result["slices"] - 1)


@pytest.mark.asyncio
async def test_audit_slice_boundaries_are_exact_and_non_overlapping():
    """审计两档的游标由一次 id-only 查询算出；按它切出来的两片必须无重、无漏。"""
    await _fresh()
    from app.db.session import get_sessionmaker
    async with get_sessionmaker()() as session:
        cursors = await journal_repo.slice_boundary_ids(session, {"groupId": GROUP}, 2)
        assert len(cursors) == 1
        first = await journal_repo.list_decision_rows(session, {"groupId": GROUP}, limit=100)
        second = await journal_repo.list_decision_rows(
            session, {"groupId": GROUP, "beforeId": cursors[0]}, limit=100)
    head = [row.id for row in first if row.id >= cursors[0]]
    tail = [row.id for row in second]
    assert set(head) & set(tail) == set()
    assert len(head) + len(tail) == TOTAL
