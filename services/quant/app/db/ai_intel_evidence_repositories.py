"""网页正文缓存与逐字引文的数据层（ai-trade-intel-evidence-grounding §5.1 / §5.2）。

`ai_intel_repositories.py` 实读 690 行、`ai_intel_citation_repositories.py` 实读 500 行，
再往任一边加这两组查询都会越 800 线；本模块的**存在本身**就是那个处置。公开函数经
`db/repositories.py` re-export，调用点仍写 `repo.X`（既有姿势，导入面单一）。

三条贯穿本模块的纪律：

1. **文档表是纯缓存，引文表是证据。** 前者可以随时清空，后者自带原文副本、必须比缓存活得久。
   两者唯一的联系是 `doc_url` 这个字符串，**没有外键、没有 join**——一条 400 天有效期的长期
   情报，它的证据不能因为 72 小时后缓存被清就消失。
2. **`quote_state` 由一个独立函数无条件写**（评审 P1-8），不是 `write_intel_quotes` 的副作用。
   必须覆盖**零引文**的条目：老库上 `ADD COLUMN quote_state VARCHAR(16) DEFAULT 'legacy'` 会让
   `'legacy'` 同时成为该表后续 INSERT 的服务端默认值，而 `_item_values` 又刻意不带这一列——
   于是一条今天入库、模型只是没给引文的条目会被界面说成「本条早于原文取证功能」。
3. **体积按字节算，不按字符算。** SQLite 的 `length()` 对 TEXT 返回**字符数**、对 BLOB 才返回
   字节数，而中文正文在 UTF-8 下是 3 字节/字。少了这道换算，行数闸允许的最坏体积是 ~240MB
   而不是设置卡上写的 80MB。
"""
from __future__ import annotations

import time

from sqlalchemy import LargeBinary, cast, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AiIntelDocument, AiIntelItem, AiIntelQuote, AiIntelRound, AppSetting

# 正文的**字节**长度。SQLite 的 `length()` 对 TEXT 返回字符数、对 BLOB 才返回字节数，而中文
# 正文在 UTF-8 下是 3 字节/字——少了这一层 CAST，行数闸允许的最坏体积是 ~240MB 而不是 80MB。
_BODY_BYTES = func.length(cast(AiIntelDocument.body, LargeBinary))

# 缓存清理时刻的落点（设置卡上「最近清理 N 小时前」那一行的真值）。
PRUNE_TS_APP_KEY = "ai_intel.docs_pruned_ts"

# 命中率徽标的默认窗口（近 N 轮）。
DEFAULT_QUOTE_STATS_ROUNDS = 20


# ---------------- wire 映射 ----------------
def intel_document_out(row: AiIntelDocument) -> dict:
    """一篇缓存文档 → camelCase wire（证据抽屉的 `docs[]`）。"""
    return {
        "url": row.url,
        "finalUrl": row.final_url or row.url,
        "domain": row.domain,
        "title": row.title or "",
        "status": row.status,
        "httpStatus": int(row.http_status or 0),
        "fetchedTs": int(row.fetched_ts or 0),
        "bodyChars": int(row.body_chars or 0),
        "truncated": bool(row.truncated),
    }


def intel_quote_out(row: AiIntelQuote) -> dict:
    """一段引文 → camelCase wire（**唯一**装配点；前端 `IntelQuote` 与它逐键同构）。"""
    return {
        "id": row.id,
        "itemId": int(row.item_id or 0),
        "instId": row.inst_id,
        "roundId": int(row.round_id or 0),
        "role": row.role or "admit",
        "ref": row.doc_ref or "",
        "text": row.text or "",
        "sourceField": row.source_field or "body",
        "docUrl": row.doc_url or "",
        "docDomain": row.doc_domain or "",
        "docPublishedTs": int(row.doc_published_ts or 0),
        "hasImperative": bool(row.has_imperative),
        "verifiedTs": int(row.verified_ts or 0),
    }


# ---------------- 文档缓存 ----------------
async def get_cached_documents(session: AsyncSession, url_hashes: list[str], *,
                               now: float, ttl_s: float,
                               negative_ttl_s: float) -> dict[str, dict]:
    """按 `url_hash` 批量取**仍然新鲜**的缓存行。

    Args:
        url_hashes: `corpus.document_key(url)` 的结果（K ≤ 12，不会撞 SQLite 的 999 变量上限）。
        now: 墙钟秒。
        ttl_s: 正缓存窗（`status == "ok"` 的行）。
        negative_ttl_s: 负缓存窗（失败结果同样入库，但窗更短，避免一个 404 被反复重抓）。

    Returns:
        ``{url_hash: wire}``，**只含新鲜行**（含新鲜的失败行——调用方据此按「无正文」处理
        而不出网）。过期行不返回，交给下一次抓取覆盖。
    """
    keys = [key for key in (url_hashes or []) if key]
    if not keys:
        return {}
    rows = (await session.execute(
        select(AiIntelDocument).where(AiIntelDocument.url_hash.in_(keys)))).scalars().all()
    wall = float(now)
    fresh: dict[str, dict] = {}
    for row in rows:
        window = float(ttl_s) if row.status == "ok" else float(negative_ttl_s)
        if wall - float(row.fetched_ts or 0) >= max(0.0, window):
            continue
        wire = intel_document_out(row)
        wire["body"] = row.body or ""
        fresh[row.url_hash] = wire
    return fresh


async def upsert_documents(session: AsyncSession, rows: list[dict], *,
                           commit: bool = True) -> int:
    """按 `url_hash` 落 / 覆盖抓取结果（含失败结果——负缓存也是缓存）。

    Args:
        rows: 形如 ``{"urlHash", "url", "finalUrl", "domain", "title", "body", "bodyChars",
            "byteLen", "status", "httpStatus", "fetchedTs", "truncated"}``。

    Returns:
        实际写入 / 覆盖的行数。
    """
    if not rows:
        return 0
    now = int(time.time())
    written = 0
    for payload in rows:
        key = str(payload.get("urlHash") or "")
        if not key:
            continue
        row = (await session.execute(
            select(AiIntelDocument).where(AiIntelDocument.url_hash == key))).scalar_one_or_none()
        if row is None:
            row = AiIntelDocument(url_hash=key, created_ts=now)
            session.add(row)
        row.url = str(payload.get("url") or "")[:600]
        row.final_url = str(payload.get("finalUrl") or payload.get("url") or "")[:600]
        row.domain = str(payload.get("domain") or "")[:128]
        row.title = str(payload.get("title") or "")[:300]
        row.body = str(payload.get("body") or "")
        row.body_chars = int(payload.get("bodyChars") or len(row.body))
        row.byte_len = int(payload.get("byteLen") or 0)
        row.status = str(payload.get("status") or "ok")[:16]
        row.http_status = int(payload.get("httpStatus") or 0)
        row.fetched_ts = int(payload.get("fetchedTs") or now)
        row.truncated = bool(payload.get("truncated"))
        written += 1
    if commit:
        await session.commit()
    return written


async def _delete_oldest(session: AsyncSession, ids: list[int]) -> int:
    if not ids:
        return 0
    await session.execute(delete(AiIntelDocument).where(AiIntelDocument.id.in_(ids)))
    return len(ids)


async def prune_documents(session: AsyncSession, *, now: float, ttl_s: float, max_rows: int,
                          max_bytes: int, commit: bool = True) -> dict[str, int]:
    """整理轮的第 6 步清理：**三道闸依次跑**（TTL → 行数 → 字节）。

    Args:
        now: 墙钟秒。
        ttl_s: 正缓存窗；本步用它的 **2 倍**做硬删线（缓存过期只是不再命中，删掉才是清理）。
        max_rows: 缓存表行数上限。
        max_bytes: 正文总**字节**预算（默认 `max_rows × body_max_chars`，恰好 80MB）。

    Returns:
        ``{"ttl": n, "rows": n, "bytes": n}``——三道闸各删了几行，逐项进轮存根的动作直方图。
    """
    counts = {"ttl": 0, "rows": 0, "bytes": 0}
    cutoff = int(float(now) - max(0.0, float(ttl_s)) * 2.0)
    stale = (await session.execute(
        select(AiIntelDocument.id).where(AiIntelDocument.fetched_ts < cutoff))).scalars().all()
    counts["ttl"] = await _delete_oldest(session, [int(i) for i in stale])
    survivors = (await session.execute(
        select(AiIntelDocument.id, _BODY_BYTES)
        .order_by(AiIntelDocument.fetched_ts.desc(), AiIntelDocument.id.desc()))).all()
    cap = max(1, int(max_rows))
    if len(survivors) > cap:
        counts["rows"] = await _delete_oldest(
            session, [int(row[0]) for row in survivors[cap:]])
        survivors = survivors[:cap]
    counts["bytes"] = await _delete_oldest(
        session, _over_budget_ids(survivors, budget=max(1, int(max_bytes))))
    await _set_pruned_ts(session, int(now))
    if commit:
        await session.commit()
    return counts


def _over_budget_ids(survivors, *, budget: int) -> list[int]:
    """按 `fetched_ts` 降序累加字节，超预算之后的（也就是更老的）那些行的 id。"""
    total = 0
    for position, row in enumerate(survivors):
        total += int(row[1] or 0)
        if total > budget:
            return [int(item[0]) for item in survivors[position + 1:]]
    return []


async def _set_pruned_ts(session: AsyncSession, now: int) -> None:
    """记下「最近一次清理」的时刻（设置卡上那一行的真值；没有它就只能显示一个假的「刚刚」）。"""
    row = await session.get(AppSetting, PRUNE_TS_APP_KEY)
    if row is None:
        session.add(AppSetting(key=PRUNE_TS_APP_KEY, value=str(int(now))))
        return
    row.value = str(int(now))


async def document_health(session: AsyncSession) -> dict:
    """设置卡「原文缓存」那一行的只读读数（**真实字节，不是前端乘出来的**）。"""
    total = int((await session.execute(
        select(func.count()).select_from(AiIntelDocument))).scalar() or 0)
    body_bytes = int((await session.execute(
        select(func.sum(_BODY_BYTES)))).scalar() or 0)
    oldest = int((await session.execute(
        select(func.min(AiIntelDocument.fetched_ts)))).scalar() or 0)
    pruned = await session.get(AppSetting, PRUNE_TS_APP_KEY)
    try:
        pruned_ts = int(pruned.value) if pruned is not None else 0
    except (TypeError, ValueError):
        pruned_ts = 0
    return {"cachedDocs": total, "bodyBytes": body_bytes, "oldestFetchedTs": oldest,
            "lastPrunedTs": pruned_ts}


async def list_documents_for_urls(session: AsyncSession, url_hashes: list[str]) -> list[dict]:
    """证据抽屉的 `docs[]`：这几个 URL 在缓存表里的**当前状态**。

    缓存已被淘汰 ⇒ 该 URL **不出现**在返回里，前端据此渲染「原文快照已过期，下方引文仍为当时
    逐字留存」——那句话必须出现，否则用户会把「没有 doc 行」误读成「这条情报没有来源」。
    """
    keys = [key for key in (url_hashes or []) if key]
    if not keys:
        return []
    rows = (await session.execute(
        select(AiIntelDocument).where(AiIntelDocument.url_hash.in_(keys))
        .order_by(AiIntelDocument.fetched_ts.desc()))).scalars().all()
    return [intel_document_out(row) for row in rows]


# ---------------- 引文 ----------------
async def write_intel_quotes(session: AsyncSession, rows: list[dict], *,
                             commit: bool = True) -> int:
    """落一批已验证引文（append-only）。

    Args:
        rows: 形如 ``{"itemId", "instId", "roundId", "role", "ref", "text", "sourceField",
            "docUrl", "docDomain", "docPublishedTs", "charStart", "hasImperative"}``。
            **`itemId` 为 0 的行一律丢弃**：没有宿主的引文行在任何界面上都不可见，只会让表
            无限长（评审 P1-7 的三条落库规则之一）。

    Returns:
        实际落库的行数。
    """
    if not rows:
        return 0
    now = int(time.time())
    written = 0
    for payload in rows:
        item_id = int(payload.get("itemId") or 0)
        if item_id <= 0:
            continue
        session.add(AiIntelQuote(
            item_id=item_id, inst_id=str(payload.get("instId") or "")[:32],
            round_id=int(payload.get("roundId") or 0),
            role=str(payload.get("role") or "admit")[:8],
            doc_ref=str(payload.get("ref") or "")[:4],
            doc_url=str(payload.get("docUrl") or "")[:600],
            doc_domain=str(payload.get("docDomain") or "")[:128],
            doc_published_ts=int(payload.get("docPublishedTs") or 0),
            source_field=str(payload.get("sourceField") or "body")[:8],
            text=str(payload.get("text") or "")[:400],
            char_start=int(payload.get("charStart") or 0),
            has_imperative=bool(payload.get("hasImperative")),
            verified_ts=int(payload.get("verifiedTs") or now)))
        written += 1
    if commit:
        await session.commit()
    return written


async def write_intel_quote_state(session: AsyncSession, states: dict[int, tuple], *,
                                  commit: bool = True) -> int:
    """对本轮**全部** accepted + merges 的 item_id **无条件**写三个引文列。

    Args:
        states: ``{item_id: (quoted, quote_count, quote_state)}``。**必须覆盖零引文的条目**
            ——见模块 docstring 第 2 条：不覆盖，一条今天入库的条目会在界面上被说成
            「本条早于原文取证功能」。

    Returns:
        实际写下去的行数。
    """
    if not states:
        return 0
    written = 0
    for item_id, triple in states.items():
        row = await session.get(AiIntelItem, int(item_id))
        if row is None:
            continue
        quoted, count, state = triple
        row.quoted = bool(quoted)
        row.quote_count = max(0, int(count))
        row.quote_state = str(state or "")[:16]
        written += 1
    if commit:
        await session.commit()
    return written


async def list_item_quotes(session: AsyncSession, item_id: int) -> list[dict]:
    """一条情报的全部引文（按 id 升序 ⇒ 采集在前、核查在后）。"""
    rows = (await session.execute(
        select(AiIntelQuote).where(AiIntelQuote.item_id == int(item_id))
        .order_by(AiIntelQuote.id.asc()))).scalars().all()
    return [intel_quote_out(row) for row in rows]


async def delete_item_quotes(session: AsyncSession, item_id: int, *,
                             commit: bool = True) -> int:
    """删一条情报的全部引文（**只在硬删条目时调**；归档是软删，条目还在，引文照留）。"""
    result = await session.execute(
        delete(AiIntelQuote).where(AiIntelQuote.item_id == int(item_id)))
    if commit:
        await session.commit()
    return int(result.rowcount or 0)


async def count_recent_quote_stats(session: AsyncSession, inst_id: str, *,
                                   limit: int = DEFAULT_QUOTE_STATS_ROUNDS) -> dict:
    """近 N 轮的引文命中率（设置卡与弹窗顶部那枚徽标的唯一取数点）。

    Returns:
        ``{"proposed": n, "verified": n, "hitRate": float | None}``。**分母 0 ⇒ `hitRate` 为
        `None` 而不是 0**：「没提过引文」与「提了全没中」在设置卡上是两句不同的话。
    """
    rows = (await session.execute(
        select(AiIntelRound.quotes_proposed, AiIntelRound.quotes_verified)
        .where(AiIntelRound.inst_id == inst_id)
        .order_by(AiIntelRound.started_ts.desc(), AiIntelRound.id.desc())
        .limit(max(1, int(limit))))).all()
    proposed = sum(int(row[0] or 0) for row in rows)
    verified = sum(int(row[1] or 0) for row in rows)
    rate = round(verified / proposed, 4) if proposed > 0 else None
    return {"proposed": proposed, "verified": verified, "hitRate": rate}
