"""三张交易日志表的读写与流式分页（ai-trade-audit-journal §3.1）。

镜像 `ai_trade_state_repositories.py` 的小文件先例：本模块只做「行 ⇄ dict」的搬运与分页，
**不做任何业务判定**（判定在 `ai_auto/journal/linking.py` 的纯函数里，编排在 `journal/service.py`）。

两条与本仓库既有实践一致的纪律：

- **列表一律 keyset 分页**（`beforeId` 游标），不用 `OFFSET`——十万行上的 `OFFSET 90000`
  在 SQLite 上是一次全扫。
- **不做 `COUNT(*)`**（除了导出预检那一处，它必须给出精确条数）。列表页不需要精确总数，
  而在过滤条件上算 COUNT 会让每一次翻页都多付一次全扫。
"""
from __future__ import annotations

import json
import time

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    AiTradeDecisionLog,
    AiTradeEventLog,
    AiTradePromptProfile,
    StrategyRun,
    Trade,
)

# 大文本列：健康卡的「实占字节」按这几列的 `LENGTH()` 求和。SQLite 没有便宜的表体积函数，
# 而这几列就是体量的全部来源（其余是标量列）。
_BYTE_COLUMNS = (
    AiTradeDecisionLog.payload_json, AiTradeDecisionLog.prompt_text,
    AiTradeDecisionLog.output_json, AiTradeDecisionLog.analysis_text,
    AiTradeDecisionLog.reflection_text, AiTradeDecisionLog.thinking_text,
    AiTradeDecisionLog.gates_json, AiTradeDecisionLog.fill_ref_json,
    AiTradeDecisionLog.context_ref_json, AiTradeDecisionLog.context_turn_json,
)


def _loads(raw: str | None):
    """解一个可能为空 / 可能坏掉的 JSON 列。**坏 JSON 不抛**——一行坏数据不该让整页 500。"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def decision_brief(row: AiTradeDecisionLog, *, link_confidence: str | None = None) -> dict:
    """列表页的 16 键摘要（§4.2 `DecisionBrief`）。"""
    return {
        "id": row.id, "groupId": row.group_id, "runId": row.run_id,
        "instId": row.inst_id, "bar": row.bar, "roundNo": row.round_no,
        "ts": row.ts, "wallTs": row.wall_ts, "trigger": row.trigger,
        "action": row.action, "finalAction": row.final_action,
        "sizeFraction": row.size_fraction, "confidence": row.confidence,
        "degradeReason": row.degrade_reason, "reflectionVerdict": row.reflection_verdict,
        "executed": bool(row.executed), "linkConfidence": link_confidence,
    }


def decision_full(row: AiTradeDecisionLog) -> dict:
    """详情页与导出共用的完整行（大文本列已解 JSON）。"""
    out = decision_brief(row)
    out.update({
        "ordType": row.ord_type, "limitPx": row.limit_px, "reason": row.reason,
        "clOrdId": row.cl_ord_id, "ordId": row.ord_id,
        "captureTier": row.capture_tier, "schemaVersion": row.schema_version,
        "payloadTruncated": bool(row.payload_truncated),
        "profileSha256": row.profile_sha256, "promptSha256": row.prompt_sha256,
        "fillRef": _loads(row.fill_ref_json), "payload": _loads(row.payload_json),
        "output": _loads(row.output_json), "gates": _loads(row.gates_json),
        "telemetry": _loads(row.telemetry_json), "contextRef": _loads(row.context_ref_json),
        "contextTurn": _loads(row.context_turn_json),
        "promptText": row.prompt_text, "analysis": row.analysis_text,
        "reflection": row.reflection_text, "thinking": row.thinking_text,
        "createdTs": row.created_ts,
    })
    return out


def event_out(row: AiTradeEventLog) -> dict:
    """一条事件行的 wire。"""
    return {"id": row.id, "groupId": row.group_id, "runId": row.run_id,
            "instId": row.inst_id, "ts": row.ts, "wallTs": row.wall_ts,
            "level": row.level, "category": row.category, "code": row.code,
            "message": row.message, "detail": _loads(row.detail_json)}


# ---------------------------------------------------------------- 写侧（drain task 专用）

async def write_journal_batch(session: AsyncSession, rows: list[dict]) -> int:
    """把一批已序列化的记录落库（**唯一写点**，由 `JournalWriter` 的单一 drain task 调用）。

    档案行（`profile` / `summaryProfile`）走 `INSERT OR IGNORE` 语义：先 `SELECT`，不存在才
    `add`。**刻意不用方言相关的 upsert**——本仓库同时要在 SQLite 与单测的内存库上跑。

    Args:
        session: 调用方开的 AsyncSession（drain task 自己持有，绝不与决策协程共用）。
        rows: `records.build_*_row()` 的产物列表。

    Returns:
        真正落库的**日志行**条数（不含档案行）。

    Raises:
        Exception: 由调用方（`writer._drain_once`）整段兜住并计入 `writeErrors`——
            一个会无限重试的审计写入，会在磁盘满的时候把整个后端拖死。
    """
    written = 0
    seen_profiles: set[str] = set()
    for row in rows:
        for key in ("profile", "summaryProfile"):
            profile = row.get(key)
            if profile and profile.get("sha256") not in seen_profiles:
                seen_profiles.add(profile["sha256"])
                await _ensure_profile(session, profile)
        if row.get("kind") == "decision":
            session.add(AiTradeDecisionLog(**row["cols"]))
            written += 1
        elif row.get("kind") == "event":
            session.add(AiTradeEventLog(**row["cols"]))
            written += 1
    await session.commit()
    return written


async def _ensure_profile(session: AsyncSession, profile: dict) -> None:
    """内容寻址档案的写一次语义（先查后写；并发重复由主键兜底）。"""
    sha = profile["sha256"]
    existing = await session.get(AiTradePromptProfile, sha)
    if existing is not None:
        return
    session.add(AiTradePromptProfile(
        sha256=sha, kind=profile.get("kind") or "profile",
        system_prompt=profile.get("systemPrompt") or "",
        tools_json=profile.get("toolsJson") or "[]",
        provider=profile.get("provider") or "", model=profile.get("model") or "",
        first_group_id=profile.get("firstGroupId") or "", created_ts=int(time.time())))


# ---------------------------------------------------------------- 读侧

def _apply_decision_filters(stmt, filters: dict):
    """把过滤条件挂到一条 decision 查询上（列表 / 计数 / 导出共用，绝不各写一份）。"""
    mapping = (("groupId", AiTradeDecisionLog.group_id),
               ("instId", AiTradeDecisionLog.inst_id),
               ("bar", AiTradeDecisionLog.bar),
               ("action", AiTradeDecisionLog.final_action),
               ("trigger", AiTradeDecisionLog.trigger))
    for key, column in mapping:
        value = filters.get(key)
        if value:
            stmt = stmt.where(column == value)
    if filters.get("runId"):
        stmt = stmt.where(AiTradeDecisionLog.run_id == int(filters["runId"]))
    if filters.get("sinceTs"):
        stmt = stmt.where(AiTradeDecisionLog.ts >= int(filters["sinceTs"]))
    if filters.get("untilTs"):
        stmt = stmt.where(AiTradeDecisionLog.ts <= int(filters["untilTs"]))
    if filters.get("executedOnly"):
        stmt = stmt.where(AiTradeDecisionLog.executed.is_(True))
    if filters.get("degradedOnly"):
        stmt = stmt.where(AiTradeDecisionLog.degrade_reason.is_not(None))
    if filters.get("gateVetoedOnly"):
        stmt = stmt.where(AiTradeDecisionLog.final_action != AiTradeDecisionLog.action)
    keyword = (filters.get("q") or "").strip()
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(AiTradeDecisionLog.reason.like(like))
    if filters.get("beforeId"):
        stmt = stmt.where(AiTradeDecisionLog.id < int(filters["beforeId"]))
    return stmt


async def list_decision_rows(session: AsyncSession, filters: dict,
                             limit: int = 50) -> list[AiTradeDecisionLog]:
    """keyset 分页的决策行（按 `id` 降序；游标是 `beforeId`）。"""
    stmt = _apply_decision_filters(select(AiTradeDecisionLog), filters)
    stmt = stmt.order_by(AiTradeDecisionLog.id.desc()).limit(max(1, int(limit)))
    return list((await session.execute(stmt)).scalars().all())


async def count_decision_rows(session: AsyncSession, filters: dict) -> int:
    """精确条数。**只给导出预检用**——列表页不需要它（见模块 docstring 第 2 条）。"""
    stmt = _apply_decision_filters(select(func.count(AiTradeDecisionLog.id)), filters)
    return int((await session.execute(stmt)).scalar() or 0)


async def get_decision_row(session: AsyncSession, row_id: int) -> AiTradeDecisionLog | None:
    return await session.get(AiTradeDecisionLog, int(row_id))


async def list_context_rows(session: AsyncSession, *, run_id: int, first_round: int,
                            until_round: int) -> list[AiTradeDecisionLog]:
    """上下文链重建用：同一 `run_id` 下 `round_no ∈ [first_round, until_round)` 的行，升序。

    走 `ix_ai_journal_dec_run_round` 这条既有索引，不需要为它再加一条。
    """
    stmt = (select(AiTradeDecisionLog)
            .where(AiTradeDecisionLog.run_id == int(run_id),
                   AiTradeDecisionLog.round_no >= int(first_round),
                   AiTradeDecisionLog.round_no < int(until_round))
            .order_by(AiTradeDecisionLog.round_no.asc(), AiTradeDecisionLog.id.asc()))
    return list((await session.execute(stmt)).scalars().all())


async def list_event_rows(session: AsyncSession, filters: dict,
                          limit: int = 50) -> list[AiTradeEventLog]:
    stmt = select(AiTradeEventLog)
    for key, column in (("groupId", AiTradeEventLog.group_id),
                        ("instId", AiTradeEventLog.inst_id),
                        ("level", AiTradeEventLog.level),
                        ("category", AiTradeEventLog.category)):
        value = filters.get(key)
        if value:
            stmt = stmt.where(column == value)
    if filters.get("sinceTs"):
        stmt = stmt.where(AiTradeEventLog.ts >= int(filters["sinceTs"]))
    if filters.get("untilTs"):
        stmt = stmt.where(AiTradeEventLog.ts <= int(filters["untilTs"]))
    if filters.get("beforeId"):
        stmt = stmt.where(AiTradeEventLog.id < int(filters["beforeId"]))
    stmt = stmt.order_by(AiTradeEventLog.id.desc()).limit(max(1, int(limit)))
    return list((await session.execute(stmt)).scalars().all())


async def get_prompt_profile(session: AsyncSession, sha256: str) -> AiTradePromptProfile | None:
    if not sha256:
        return None
    return await session.get(AiTradePromptProfile, sha256)


async def load_profiles(session: AsyncSession, shas: list[str]) -> dict[str, dict]:
    """一次取回一批档案（导出侧批量装配用；缺席的 sha 不出现在结果里）。"""
    wanted = [s for s in {s for s in shas if s}]
    if not wanted:
        return {}
    stmt = select(AiTradePromptProfile).where(AiTradePromptProfile.sha256.in_(wanted))
    rows = (await session.execute(stmt)).scalars().all()
    return {row.sha256: {"kind": row.kind, "systemPrompt": row.system_prompt,
                         "toolsJson": row.tools_json, "provider": row.provider,
                         "model": row.model} for row in rows}


async def journal_table_stats(session: AsyncSession) -> dict:
    """三张表的行数与大文本列的实占字节（健康卡直接显示，不让用户靠猜）。"""
    decisions = int((await session.execute(
        select(func.count(AiTradeDecisionLog.id)))).scalar() or 0)
    events = int((await session.execute(select(func.count(AiTradeEventLog.id)))).scalar() or 0)
    profiles = int((await session.execute(
        select(func.count(AiTradePromptProfile.sha256)))).scalar() or 0)
    total = 0
    for column in _BYTE_COLUMNS:
        total += int((await session.execute(
            select(func.coalesce(func.sum(func.length(column)), 0)))).scalar() or 0)
    return {"decisions": decisions, "events": events, "promptProfiles": profiles,
            "approxBytes": total}


async def list_journal_sessions(session: AsyncSession, *, limit: int = 30,
                                cursor_ts: int | None = None) -> dict:
    """会话选择器数据源。

    与既有 `/ai-trade/history` 的区别是硬性的：后者**刻意排除当前运行组**
    （`ai_auto_repositories.list_ai_trade_groups` 的既有语义），而日志台必须能看正在跑的那一个
    ——「刚才那一轮为什么这么决策」是审计最常被问到的问题，而它问的恰恰是还在跑的那个会话。
    """
    stmt = (select(StrategyRun.group_id, StrategyRun.bar,
                   func.min(StrategyRun.start_ts).label("started_ts"),
                   func.max(func.coalesce(StrategyRun.end_ts, 0)).label("ended_ts"))
            .where(StrategyRun.group_id.is_not(None), StrategyRun.mode == "paper")
            .group_by(StrategyRun.group_id))
    if cursor_ts:
        stmt = stmt.having(func.min(StrategyRun.start_ts) < int(cursor_ts))
    stmt = stmt.order_by(func.min(StrategyRun.start_ts).desc()).limit(max(1, int(limit)))
    heads = list((await session.execute(stmt)).all())
    group_ids = [row[0] for row in heads if row[0]]
    if not group_ids:
        return {"items": [], "nextCursorTs": None}
    runs = list((await session.execute(
        select(StrategyRun).where(StrategyRun.group_id.in_(group_ids)))).scalars().all())
    dec_counts = dict((await session.execute(
        select(AiTradeDecisionLog.group_id, func.count(AiTradeDecisionLog.id))
        .where(AiTradeDecisionLog.group_id.in_(group_ids))
        .group_by(AiTradeDecisionLog.group_id))).all())
    evt_counts = dict((await session.execute(
        select(AiTradeEventLog.group_id, func.count(AiTradeEventLog.id))
        .where(AiTradeEventLog.group_id.in_(group_ids))
        .group_by(AiTradeEventLog.group_id))).all())
    items = []
    for group_id, bar, started_ts, ended_ts in heads:
        if not group_id:
            continue
        members = [r for r in runs if r.group_id == group_id]
        items.append({
            "groupId": group_id, "bar": bar or "",
            "status": _group_status(members),
            "instIds": sorted({r.inst_id for r in members}),
            "runIds": sorted(r.id for r in members),
            "startedTs": int(started_ts or 0),
            "endedTs": int(ended_ts) or None,
            "decisionRows": int(dec_counts.get(group_id, 0)),
            "eventRows": int(evt_counts.get(group_id, 0)),
        })
    return {"items": items,
            "nextCursorTs": items[-1]["startedTs"] if len(items) >= limit else None}


def _group_status(members: list[StrategyRun]) -> str:
    """分组状态：任一成员在跑即 running（与 `ai_auto_repositories._group_status` 同一条判据）。"""
    statuses = {r.status for r in members}
    for candidate in ("running", "error", "stopped"):
        if candidate in statuses:
            return candidate
    return "done" if statuses else "unknown"


async def list_run_trades(session: AsyncSession, run_ids: list[int]) -> dict[int, list[dict]]:
    """按 `run_id` 取回成交腿，**按 `trades.id` 升序**。

    ⚠️ 排序键必须是 `trades.id`，**绝不能是 `trades.ts`**。这不是风格问题：AI 自动交易的
    `trades.ts` 由**两套时钟**写入（K 线轮 = bar 开盘秒 / 定时唤醒轮与盯市轮 = 墙钟），按 `ts`
    排序会破坏真实的开→平交替。这条已在 `ai_auto_repositories._pair_round_trips` 的 docstring
    里被完整记录（「实测生产库按 id 配对 161 笔平仓零孤儿」），本函数逐字沿用同一条判据。
    """
    if not run_ids:
        return {}
    stmt = (select(Trade).where(Trade.run_id.in_(run_ids))
            .order_by(Trade.run_id.asc(), Trade.id.asc()))
    out: dict[int, list[dict]] = {}
    for trade in (await session.execute(stmt)).scalars().all():
        out.setdefault(trade.run_id, []).append(trade_row(trade))
    return out


def trade_row(trade: Trade) -> dict:
    """一条成交腿的 wire（含 `explain_json.meta`——往返标签的唯一真源就在那里）。"""
    explain = _loads(trade.explain_json) or {}
    meta = explain.get("meta") if isinstance(explain, dict) else None
    return {"id": trade.id, "runId": trade.run_id, "ts": trade.ts, "side": trade.side,
            "price": trade.price, "qty": trade.qty, "fee": trade.fee, "pnl": trade.pnl,
            "positionAfter": trade.position_after, "reason": trade.reason,
            "positionPct": trade.position_pct, "peakPnl": trade.peak_pnl,
            "meta": meta if isinstance(meta, dict) else {}}


# ---------------------------------------------------------------- 剪枝

async def protected_group_ids(session: AsyncSession) -> list[str]:
    """**绝对禁止删除**其日志的分组：run 处于 `running` 的那些。

    正在跑的会话的日志被删掉，是审计系统能犯的最严重的错。判据取库侧的 run 状态；内存 manager
    侧的那一半由 `journal/retention.py` 在调用点合并（两处并集，宁可少删）。
    """
    stmt = (select(StrategyRun.group_id).where(StrategyRun.group_id.is_not(None),
                                               StrategyRun.status == "running").distinct())
    return [row[0] for row in (await session.execute(stmt)).all() if row[0]]


async def prune_journal(session: AsyncSession, *, before_ts: int | None, max_rows: int,
                        protected: list[str], dry_run: bool) -> dict:
    """双闸剪枝（天数 + 行数），**按最老优先**，且绝不碰受保护分组的行。

    Args:
        before_ts: 早于此秒的行可删；`None` ⇒ 天数闸不生效。
        max_rows: 决策行总数上限；`0` ⇒ 行数闸不生效。
        protected: 绝不删除的 `group_id` 列表。
        dry_run: 只数不删。

    Returns:
        `{"decisions": n, "events": n}`（`dry_run` 时是「将会删掉多少」）。
    """
    decisions = await _collect_prunable(session, AiTradeDecisionLog, before_ts=before_ts,
                                        max_rows=max_rows, protected=protected)
    events = await _collect_prunable(session, AiTradeEventLog, before_ts=before_ts,
                                     max_rows=0, protected=protected)
    if not dry_run and (decisions or events):
        if decisions:
            await session.execute(delete(AiTradeDecisionLog)
                                  .where(AiTradeDecisionLog.id.in_(decisions)))
        if events:
            await session.execute(delete(AiTradeEventLog).where(AiTradeEventLog.id.in_(events)))
        await session.commit()
    return {"decisions": len(decisions), "events": len(events)}


async def _collect_prunable(session: AsyncSession, model, *, before_ts: int | None,
                            max_rows: int, protected: list[str]) -> list[int]:
    """两闸各自选出待删 id 后**取并集**（任一闸命中即可删），受保护分组一律剔除。"""
    ids: set[int] = set()
    base = select(model.id)
    if protected:
        base = base.where(model.group_id.notin_(protected))
    if before_ts:
        stmt = base.where(model.created_ts > 0, model.created_ts < int(before_ts))
        ids.update(int(row[0]) for row in (await session.execute(stmt)).all())
    if max_rows and max_rows > 0:
        total = int((await session.execute(select(func.count(model.id)))).scalar() or 0)
        overflow = total - int(max_rows)
        if overflow > 0:
            stmt = base.order_by(model.id.asc()).limit(overflow)
            ids.update(int(row[0]) for row in (await session.execute(stmt)).all())
    return sorted(ids)


async def slice_boundary_ids(session: AsyncSession, filters: dict, slices: int) -> list[int]:
    """把一次导出切成 `slices` 片时，第 2..N 片的 `beforeId` 游标（**精确，不是估的**）。

    没有它，分片就是假的：浏览器导航式的下载读不回 `eof.nextBeforeId`，前端只能一片片从头
    拉，最后得到 N 个一模一样的文件——比不分片更坏。

    这里**刻意用 `OFFSET`**，与本模块「列表一律 keyset」的纪律不冲突：那条纪律针对的是每次
    翻页都要付一遍代价的**列表**，而这里是一次导出至多算 N-1 次、且只取一列 `id`（走索引，
    不回表）。N 是片数（个位数），代价可控。

    Args:
        session: 调用方开的 AsyncSession。
        filters: 与导出**完全同一份**过滤条件（排序也必须同为 `id DESC`，否则游标对不上）。
        slices: 片数；≤ 1 即返回空列表。

    Returns:
        长度 ≤ `slices - 1` 的游标列表（行数不够切时短于它）。
    """
    if slices <= 1:
        return []
    total = await count_decision_rows(session, filters)
    if total <= 0:
        return []
    per_slice = max(1, -(-total // slices))          # ceil(total / slices)
    cursors: list[int] = []
    for index in range(1, slices):
        boundary = index * per_slice - 1
        # 同 `export_sft.slice_cursors`：边界落在最后一行上时那一片会是空文件，故少发一片。
        if boundary >= total - 1:
            break
        stmt = _apply_decision_filters(select(AiTradeDecisionLog.id), filters)
        stmt = stmt.order_by(AiTradeDecisionLog.id.desc()).limit(1).offset(boundary)
        row_id = (await session.execute(stmt)).scalars().first()
        if row_id is None:
            break
        cursors.append(int(row_id))
    return cursors
