"""`audit-csv` / `audit-jsonl` 的格式契约（ai-trade-audit-journal §2.5.1）。

其中 `test_csv_escaping_matches_the_shared_golden_fixture` 是本文件的核心：CSV 转义在前后端
各有一份实现，而**一个进程里没有另一份**（后端跑 pytest、前端跑 vitest），且前端的
`escapeField` 是模块私有的。故两侧断言的是**同一组提交进仓库的夹具文件**——任一侧漂移都会在
自己的 CI 上判红，而夹具本身的改动必然出现在 diff 里、必须两侧同时改（评审 RP1-9）。
"""
from __future__ import annotations

import json
import pathlib
import time

import pytest
from sqlalchemy import delete

from app.ai_auto.journal.export_audit import (
    BOM,
    CSV_COLUMNS,
    ROW_SEP,
    TRUNCATED_MARKER,
    ExportBudget,
    csv_line,
    escape_field,
    stream_audit_csv,
    stream_audit_jsonl,
)
from app.db.models import AiTradeDecisionLog, AiTradeEventLog, AiTradePromptProfile
from app.db.session import get_sessionmaker, init_db

from .test_ai_trade_journal_api import GROUP, NOW, decision_row

_FIXTURES = (pathlib.Path(__file__).resolve().parents[3] / "docs" / "plans"
             / "ai-trade-audit-journal" / "fixtures")


async def _fresh_with(rows: int) -> None:
    await init_db()
    async with get_sessionmaker()() as session:
        for table in (AiTradeDecisionLog, AiTradeEventLog, AiTradePromptProfile):
            await session.execute(delete(table))
        session.add(AiTradePromptProfile(sha256="p" * 64, kind="profile", system_prompt="SYS",
                                         tools_json="[]", provider="anthropic",
                                         model="m", first_group_id=GROUP, created_ts=NOW))
        for index in range(1, rows + 1):
            session.add(decision_row(index))
        await session.commit()


async def _collect(stream) -> str:
    return "".join([chunk async for chunk in stream])


def test_csv_escaping_matches_the_shared_golden_fixture():
    """读共享黄金夹具，断言本侧实现**逐字节命中**。前端用例读同两个文件。"""
    cases = json.loads((_FIXTURES / "csv_escape_cases.json").read_text(encoding="utf-8"))
    # **按字节读**：文本模式的通用换行会把 CRLF 归一成 LF，而这道门禁要守的恰恰是行分隔符
    # 本身——按文本读等于两侧都按 LF 比，门禁当场空转。
    golden = (_FIXTURES / "csv_escape_golden.csv").read_bytes().decode("utf-8")
    lines = [escape_field(cases["column"]["label"])]
    lines += [escape_field(value) for value in cases["values"]]
    assert BOM + ROW_SEP.join(lines) == golden
    # `undefinedIndices` 指的那些位置在前端要被替换成 `undefined`；两侧都必须归空串。
    for index in cases["undefinedIndices"]:
        assert escape_field(cases["values"][index]) == ""


def test_csv_line_uses_crlf_and_rfc4180_quoting():
    assert csv_line(["a,b", 'say "hi"', "plain"]) == '"a,b","say ""hi""",plain'
    assert ROW_SEP == "\r\n"


@pytest.mark.asyncio
async def test_csv_stream_has_bom_and_twenty_two_columns():
    await _fresh_with(2)
    text = await _collect(stream_audit_csv({"groupId": GROUP}, ExportBudget()))
    assert text.startswith(BOM)
    header = text[len(BOM):].split(ROW_SEP)[0]
    assert header.split(",") == list(CSV_COLUMNS)
    assert len(CSV_COLUMNS) == 22
    body = [line for line in text.split(ROW_SEP)[1:] if line]
    assert len(body) == 2


@pytest.mark.asyncio
async def test_ndjson_stream_is_enveloped_by_manifest_and_eof():
    """被网络截断的 NDJSON 与完整的在语法上没有任何区别——**没有 eof 行即视为不完整**。"""
    await _fresh_with(2)
    text = await _collect(stream_audit_jsonl({"groupId": GROUP}, ExportBudget(), {}))
    lines = [json.loads(line) for line in text.splitlines() if line]
    assert lines[0]["kind"] == "manifest"
    assert lines[-1]["kind"] == "eof"
    assert lines[-1]["complete"] is True
    assert lines[-1]["truncatedReason"] == "none"
    kinds = [row["kind"] for row in lines]
    assert kinds.count("decision") == 2
    assert "promptProfile" in kinds, "档案行必须在它的引用者之前到达"
    assert kinds.index("promptProfile") < kinds.index("decision")


@pytest.mark.asyncio
async def test_decision_lines_carry_their_outcome_instead_of_a_second_copy():
    """成交 / 订单 / 往返不单独出行：再发一遍就是同一事实的第二份记录。"""
    await _fresh_with(1)
    text = await _collect(stream_audit_jsonl({"groupId": GROUP}, ExportBudget(), {}))
    decision = next(json.loads(line) for line in text.splitlines()
                    if json.loads(line)["kind"] == "decision")
    assert "outcome" in decision
    assert set(decision["outcome"]) == {"linkConfidence", "fill", "trip", "noFillReason",
                                        "fillSource"}


@pytest.mark.asyncio
async def test_csv_truncation_appends_a_visible_marker_row():
    """一个静默截断的 CSV 正是本节要消灭的东西：Excel 会把这一行显示成一条普通行。"""
    await _fresh_with(4)
    budget = ExportBudget(page_size=2, max_rows=2, time_budget_s=0.0)
    text = await _collect(stream_audit_csv({"groupId": GROUP}, budget))
    last = [line for line in text.split(ROW_SEP) if line][-1]
    assert last.startswith(TRUNCATED_MARKER)
    assert last.split(",")[1] == "max_rows"
    assert last.split(",")[2].isdigit(), "尾注第三列必须是下一片的游标"


@pytest.mark.asyncio
async def test_eof_admits_when_the_event_stream_was_cut_by_the_budget():
    """事件段与决策段共享同一份墙钟预算：它被截断时 `eof` 必须跟着说实话。

    一个 `complete: true` 却少了一半事件行的文件，正是完整性信封要消灭的东西——
    被截断的 NDJSON 与完整的在语法上没有任何区别。
    """
    await _fresh_with(1)
    async with get_sessionmaker()() as session:
        for index in range(4):
            session.add(AiTradeEventLog(group_id=GROUP, ts=NOW + index, wall_ts=NOW,
                                        level="info", category="lifecycle",
                                        code="GROUP_STARTED", message="x", created_ts=NOW))
        await session.commit()
    # 决策段跑得完（page_size 够大），事件段一进来就撞墙钟。
    budget = ExportBudget(page_size=50, max_rows=1000, time_budget_s=1e-9)
    text = await _collect(stream_audit_jsonl({"groupId": GROUP}, budget, {}))
    eof = json.loads(text.splitlines()[-1])
    assert eof["kind"] == "eof"
    assert eof["complete"] is False
    assert eof["truncatedReason"] == "time_budget"
    assert eof["eventsEmitted"] == 0


@pytest.mark.asyncio
async def test_eof_counts_events_separately_from_decisions():
    await _fresh_with(1)
    async with get_sessionmaker()() as session:
        session.add(AiTradeEventLog(group_id=GROUP, ts=NOW, wall_ts=NOW, level="info",
                                    category="lifecycle", code="GROUP_STARTED",
                                    message="x", created_ts=NOW))
        await session.commit()
    text = await _collect(stream_audit_jsonl({"groupId": GROUP}, ExportBudget(), {}))
    eof = json.loads(text.splitlines()[-1])
    assert eof["emitted"] == 1 and eof["eventsEmitted"] == 1
    assert eof["complete"] is True


@pytest.mark.asyncio
async def test_manifest_carries_the_scope_and_generation_stamp():
    await _fresh_with(1)
    text = await _collect(stream_audit_jsonl({"groupId": GROUP}, ExportBudget(),
                                             {"scope": {"groupId": GROUP}}))
    manifest = json.loads(text.splitlines()[0])
    assert manifest["format"] == "audit-jsonl"
    assert manifest["scope"]["groupId"] == GROUP
    assert manifest["generatedTs"] <= int(time.time())
