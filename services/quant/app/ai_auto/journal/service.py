"""读侧编排（ai-trade-audit-journal §2.4.1）。

只做「查询参数 → 仓储调用 → wire 装配」，**不做任何业务判定**——判定在 `linking.py` 的纯函数
里。列表端点一律 keyset 分页（`beforeId` 游标），不用 `OFFSET`。
"""
from __future__ import annotations

import hashlib
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ...config import Settings
from ...db import ai_journal_repositories as journal_repo
from ..prompts import build_decision_prompt
from . import linking
from .runtime import get_journal_writer, capture_limits_of

logger = logging.getLogger(__name__)

# `q` 的长度上界（超出即 422）。它进的是 SQL `LIKE`，无界的关键字会把十万行上的扫描拖成分钟级。
MAX_QUERY_CHARS = 64


async def list_decisions(session: AsyncSession, filters: dict, limit: int) -> dict:
    """决策轮次列表。`total` **恒为 `null`**：十万行上的 `COUNT(*)` + 过滤会让每次翻页都变慢，
    而列表页不需要精确总数（需要它的只有导出预检，那里单独算）。"""
    rows = await journal_repo.list_decision_rows(session, filters, limit=limit)
    briefs = [journal_repo.decision_brief(row) for row in rows]
    fulls = [journal_repo.decision_full(row) for row in rows]
    trades = await journal_repo.list_run_trades(session, sorted({r.run_id for r in rows}))
    for brief, link in zip(briefs, linking.link_decisions_to_fills(fulls, trades)):
        brief["linkConfidence"] = link["linkConfidence"]
    return {"items": briefs,
            "nextBeforeId": rows[-1].id if len(rows) >= limit else None,
            "total": None}


async def get_decision(session: AsyncSession, row_id: int, settings: Settings) -> dict | None:
    """单条决策的七段详情（结论 / 门禁链 / 上下文 / 提示词 / 模型输出 / 执行与结果 / 遥测）。"""
    row = await journal_repo.get_decision_row(session, row_id)
    if row is None:
        return None
    full = journal_repo.decision_full(row)
    trades = await journal_repo.list_run_trades(session, [row.run_id])
    link = linking.link_one(full, trades.get(row.run_id, []))
    gates = full.get("gates") or {}
    return {
        "brief": journal_repo.decision_brief(row, link_confidence=link["linkConfidence"]),
        "gates": gates.get("layers") or [],
        "vetoedBy": gates.get("vetoedBy"),
        "payload": full.get("payload"), "payloadTruncated": full.get("payloadTruncated"),
        "output": full.get("output"),
        "analysis": full.get("analysis"), "reflection": full.get("reflection"),
        "thinking": full.get("thinking"),
        "prompt": _prompt_block(full),
        "telemetry": full.get("telemetry") or {},
        "contextRef": full.get("contextRef"),
        "fillRef": full.get("fillRef"),
        "reason": full.get("reason"), "ordType": full.get("ordType"),
        "limitPx": full.get("limitPx"), "captureTier": full.get("captureTier"),
        "outcome": {"linkConfidence": link["linkConfidence"], "fill": link["fill"],
                    "trip": link["trip"], "noFillReason": link["noFillReason"],
                    "fillSource": link["fillSource"],
                    "order": await _order_receipt(session, full, settings)},
    }


def _prompt_block(full: dict) -> dict:
    """详情页第 4 段的提示词块。

    `full` 档存了原文 ⇒ 直接给（`source="stored"`，不受渲染代码演进影响）；
    `standard` 档只存了 sha ⇒ 给 `source="rebuildable"`，由前端按需调
    `GET /decisions/{id}/prompt` 重建并比对；`minimal` 档 ⇒ `source="none"`。
    """
    if full.get("promptText"):
        return {"source": "stored", "text": full["promptText"]}
    if full.get("payload") is not None:
        return {"source": "rebuildable", "storedSha": full.get("promptSha256") or ""}
    return {"source": "none"}


async def rebuild_prompt(session: AsyncSession, row_id: int) -> dict | None:
    """按存档 `payload` 重新渲染提示词，并与存档 sha 逐字比对（§4.4）。

    `shaMatch=false` **不是错误**（仍返回 200）：重建出来的文本仍有参考价值。但前端**必须**
    显著告知「提示词渲染代码在此之后已变更 / 该轮带过重试后缀，下方与当时喂给模型的
    不完全相同」——这句话直接决定用户会不会拿一批假样本去训练。

    Returns:
        `None` ⇒ 行不存在（端点回 404）；`{"error": "payload_absent"}` ⇒ 该轮以 `minimal` 档
        采集，没有上下文可供重建（端点回 409）。
    """
    row = await journal_repo.get_decision_row(session, row_id)
    if row is None:
        return None
    full = journal_repo.decision_full(row)
    if full.get("promptText"):
        stored = full.get("promptSha256") or ""
        return {"text": full["promptText"], "shaMatch": True,
                "storedSha": stored, "renderedSha": stored, "source": "stored"}
    payload = full.get("payload")
    if payload is None:
        return {"error": "payload_absent"}
    text = build_decision_prompt(payload)
    rendered = hashlib.sha256(text.encode("utf-8")).hexdigest()
    stored = full.get("promptSha256") or ""
    return {"text": text, "shaMatch": bool(stored) and rendered == stored,
            "storedSha": stored, "renderedSha": rendered, "source": "rendered"}


async def _order_receipt(session: AsyncSession, full: dict, settings: Settings) -> dict | None:
    """订单回执（`live_order_audit` 按 `cl_ord_id` 直查）。

    ⚠️ 该表的索引是 `ix_live_audit_account("account_id", "id")`——`created_ts` **不在索引里**
    （评审 RP2-5）。本轮**不加索引**（加索引 = 动既有表，违反「零 DB 迁移」纪律），
    故按 `cl_ord_id` 的等值过滤在当前量级下直接扫，量级可控。
    """
    cl_ord_id = str(full.get("clOrdId") or "")
    if not cl_ord_id:
        return None
    from sqlalchemy import select

    from ...db.models import LiveOrderAudit
    stmt = (select(LiveOrderAudit).where(LiveOrderAudit.cl_ord_id == cl_ord_id)
            .order_by(LiveOrderAudit.id.desc()).limit(1))
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        return None
    return {"id": row.id, "instId": row.inst_id, "direction": row.direction,
            "ordType": row.ord_type, "px": row.px, "sz": row.sz, "status": row.status,
            "clOrdId": row.cl_ord_id, "ordId": row.ord_id, "errorCode": row.error_code,
            "createdTs": row.created_ts}


async def list_events(session: AsyncSession, filters: dict, limit: int) -> dict:
    rows = await journal_repo.list_event_rows(session, filters, limit=limit)
    return {"items": [journal_repo.event_out(row) for row in rows],
            "nextBeforeId": rows[-1].id if len(rows) >= limit else None}


async def list_fills(session: AsyncSession, filters: dict, limit: int) -> dict:
    """成交与订单页。每条带 `decisionRef`——**成交找得回决策**是审计的另一半。"""
    dec_filters = dict(filters)
    dec_filters["executedOnly"] = True
    rows = await journal_repo.list_decision_rows(session, dec_filters, limit=limit)
    fulls = [journal_repo.decision_full(row) for row in rows]
    trades = await journal_repo.list_run_trades(session, sorted({r.run_id for r in rows}))
    items = []
    for row, full, link in zip(rows, fulls, linking.link_decisions_to_fills(fulls, trades)):
        items.append({
            "decisionRef": {"id": row.id, "roundNo": row.round_no,
                            "linkConfidence": link["linkConfidence"]},
            "groupId": row.group_id, "runId": row.run_id, "instId": row.inst_id,
            "ts": row.ts, "wallTs": row.wall_ts, "action": row.final_action,
            "clOrdId": row.cl_ord_id, "ordId": row.ord_id,
            "fill": link["fill"], "trip": link["trip"], "fillSource": link["fillSource"],
            "legs": (full.get("fillRef") or {}).get("legs") or [],
        })
    return {"items": items, "nextBeforeId": rows[-1].id if len(rows) >= limit else None}


async def journal_stats(session: AsyncSession, settings: Settings) -> dict:
    """健康度与口径读数（§4.6）。**前端的每一个口径数字都来自这里，一个都不硬编码。**"""
    writer_stats = get_journal_writer().stats()
    tables = await journal_repo.journal_table_stats(session)
    limits = capture_limits_of(settings)
    return {
        "enabled": writer_stats["enabled"],
        "captureTier": limits.tier,
        "contextChainEnabled": limits.context_chain,
        "queue": writer_stats["queue"],
        "counters": writer_stats["counters"],
        "tables": tables,
        "retention": {"days": int(settings.ai_trade_journal_retention_days),
                      "maxRows": int(settings.ai_trade_journal_max_rows)},
        "limits": {"textMaxChars": limits.text_max_chars,
                   "payloadMaxChars": limits.payload_max_chars,
                   "exportMaxRows": int(settings.ai_trade_journal_export_max_rows),
                   "exportPageSize": int(settings.ai_trade_journal_export_page_size),
                   "exportTimeBudgetS": float(
                       settings.ai_trade_journal_export_time_budget_s),
                   "eventMinLevel": str(settings.ai_trade_journal_event_min_level)},
    }
