"""情报告警台账的数据层（ai-trade-intel-actionability §5.1）。

`ai_intel_repositories.py` 实读 707 行、`ai_intel_citation_repositories.py` 493 行，再往任一个
里加这五个函数都会逼近 800 线；本模块的**存在本身**就是「不让它们越线」的处置（§十二 已登记）。
公开函数经 `db/repositories.py` re-export，调用点仍写 `repo.X`（导入面单一）。

三条贯穿本模块的纪律：

1. **三个写点，每一个独占一组列**——纪律不是「只许写一次」，而是「同一列只有一个人写」，
   那才是丢更新的来源：`write_intel_alert`（创建，跑在 `_persist_collect` 的同一次 commit
   里；`outcome` 恒为 ``""``，**绝不预填**）、`stamp_intel_alert_digest`（只写 `ref_token` /
   `in_digest`——两者在建行那一刻还不知道，digest 要等 `IntelRuntime.refresh` 重算完才有编号）、
   `mark_intel_alert_outcome`（只写 `outcome` 那一组四列）。
2. **冷却时刻与日配额从本表现算**，不落 `ai_intel_watch` 的第二份账（§5.1「为什么零 ALTER」）：
   `ai_intel_watch` 上的 `day_key` / `day_rounds` 已经因为「谁写谁清」的语义引出过一次缺陷。
3. **回写是幂等的**：`mark_intel_alert_outcome` 只写 `outcome == ""` 的行，一条已经结账的
   告警不会被后来的清理分支改写。唯一的例外是 `superseded`——它由覆盖那一刻立即写，且此后
   那个 handoff 就不在会话手上了。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .ai_intel_repositories import utc_day_key
from .models import AiIntelAlert


def _loads(raw: str | None, fallback):
    """JSON 列 → Python；缺 / 空 / 坏 JSON 一律回落（沿用既有容错口径）。"""
    if not raw:
        return fallback
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def intel_alert_out(row: AiIntelAlert) -> dict:
    """一行告警台账 → camelCase wire（**唯一**装配点）。

    ⚠️ 两个文案字段（`suppressedText` / `outcomeText`）**由后端下发**，跟随 `degrade_wire` 的
    既有裁决：进 wire 的文案由后端管，只在轮存根直方图里出现的码才由前端 `CODE_LABEL` 管。
    不这么做就是第三次「后端加了码、前端没登记、用户看到裸 slug」。

    **绝不出现在 wire 上的东西**：URL、域名、原文正文、检索源 API key。
    """
    from ..ai_auto.intel import taxonomy as tax

    return {
        "id": row.id,
        "instId": row.inst_id or "",
        "groupId": row.group_id or "",
        "itemId": int(row.item_id or 0),
        "roundId": int(row.round_id or 0),
        "createdTs": int(row.created_ts or 0),
        "severity": round(float(row.severity or 0.0), 4),
        "fired": bool(row.fired),
        "suppressedCode": row.suppressed_code or "",
        "suppressedText": tax.alert_suppress_label(row.suppressed_code or ""),
        "outcome": row.outcome or "",
        "outcomeText": tax.alert_outcome_label(row.outcome or ""),
        "outcomeTs": int(row.outcome_ts or 0),
        "runId": int(row.run_id or 0),
        "roundNo": int(row.round_no or 0),
        "headline": row.headline or "",
        "category": row.category or "",
        "tier": row.tier or "",
        "stance": row.stance or "",
        "impact": row.impact or "",
        "domainCount": int(row.domain_count or 0),
        "quoted": bool(row.quoted),
        "confidence": round(float(row.confidence or 0.0), 4),
        "ref": row.ref_token or "",
        # ★ 与 `ref` 是两件事：`cite` 关时全库都没有编号，条目却照样在情报段里。前端的
        # 「未进入情报段」那句话只许读这一个键——用 `!ref` 冒充它，就是把后端 P0-3 花了整节
        # 去禁止的那次混淆搬到界面上（届时每一条告警都会挂着一句关于数据的假话）。
        "inDigest": bool(row.in_digest),
        "reasons": _loads(row.reasons_json, {}),
    }


async def write_intel_alert(session: AsyncSession, payload: dict, *,
                            commit: bool = True) -> int:
    """写一行告警台账，返回它的 `id`。

    ⚠️ **必须接住返回值**：`AlertHandoff.alert_id` 是会话侧回写 `outcome` 的唯一途径，出了
    `_persist_collect` 的那个 `async with` 就再也拿不到（与 `upsert_intel_items` 同一条纪律）。

    Args:
        session: 调用方持有的写会话（**与条目落库同一个**，评审 P1-5：台账行必须与条目行同生
            共死——条目回滚而告警行留下，等于凭空多出一条指向不存在条目的告警）。
        payload: camelCase 字典（键名与 `intel_alert_out` 对称）。
        commit: 由调用方决定；`_persist_collect` 传 ``False`` 并在最后一次性提交。

    Returns:
        新行的自增 `id`。
    """
    row = AiIntelAlert(
        inst_id=str(payload.get("instId") or ""),
        group_id=str(payload.get("groupId") or ""),
        item_id=int(payload.get("itemId") or 0),
        round_id=int(payload.get("roundId") or 0),
        created_ts=int(payload.get("createdTs") or time.time()),
        severity=float(payload.get("severity") or 0.0),
        fired=bool(payload.get("fired")),
        suppressed_code=str(payload.get("suppressedCode") or "")[:24],
        # ★ 创建时恒为 ""：绝不预填一个「尚未发生」的结果（见模块 docstring 纪律 1）。
        outcome="",
        outcome_ts=0,
        run_id=0,
        round_no=0,
        headline=str(payload.get("headline") or "")[:160],
        category=str(payload.get("category") or "")[:16],
        tier=str(payload.get("tier") or "")[:8],
        stance=str(payload.get("stance") or "")[:8],
        impact=str(payload.get("impact") or "")[:8],
        domain_count=int(payload.get("domainCount") or 0),
        quoted=bool(payload.get("quoted")),
        confidence=float(payload.get("confidence") or 0.0),
        ref_token=str(payload.get("ref") or "")[:4],
        reasons_json=json.dumps(payload.get("reasons") or {}, ensure_ascii=False),
    )
    session.add(row)
    await session.flush()
    if commit:
        await session.commit()
    return int(row.id)


async def stamp_intel_alert_digest(session: AsyncSession, alert_id: int, *, ref: str,
                                   in_digest: bool, commit: bool = True) -> bool:
    """补写「本轮编号」与「是否真的进了情报段」（台账的**第二个**写点，只碰这两列）。

    为什么不能在建行时一并写：`ref` 与 `in_digest` 都要从**重算之后**的 digest 反查，而那次
    重算（`IntelRuntime.refresh`）发生在采集轮提交**之后**。少了这一跳，两列就是「有列、有
    读点、没有写点」——`GET /alerts` 上 `ref` 恒为空、`inDigest` 恒为假，动态页签于是给每一条
    告警都挂上一句「未进入情报段」。

    Args:
        alert_id: 台账行 id（来自 `AlertHandoff.alert_id`）。
        ref: 本轮注入编号（``"I02"``）；`cite` 关时为空串（**空串是合法取值**，不是「没写」）。
        in_digest: 这一条是否真的被收进了 `build_digest` 的 `lines`。
        commit: 由调用方决定。

    Returns:
        是否真的写到了一行（``False`` = 行不存在）。**不做「已写过就跳过」的守卫**：这两列
        除本函数外没有第二个写点，而每条 handoff 只经过一次第三跳。
    """
    row = await session.get(AiIntelAlert, int(alert_id))
    if row is None:
        return False
    row.ref_token = str(ref or "")[:4]
    row.in_digest = bool(in_digest)
    if commit:
        await session.commit()
    return True


async def mark_intel_alert_outcome(session: AsyncSession, alert_id: int, *, outcome: str,
                                   outcome_ts: int | None = None, run_id: int = 0,
                                   round_no: int = 0, commit: bool = True) -> bool:
    """回写一条告警的结果（台账的**第三个也是最后一个**写点，只碰 `outcome` 那一组四列）。

    **幂等**：只写 `outcome == ""` 的行。一条已经结账的告警不会被后来的清理分支改写——
    `deferred` 之后真的起了轮时，会话侧会再调一次本函数，那一次也**不会**覆盖 `deferred`
    （评审建议 2 的成文取舍：台账要的是「最终发生了什么」，而动态页签的文案因此不得写成
    「本次未被推迟过」，它说不出这句话）。

    Args:
        alert_id: 台账行 id（来自 `AlertHandoff.alert_id`）。
        outcome: `taxonomy.ALERT_OUTCOMES` 的元素。
        outcome_ts: 结账时刻；缺省取当前墙钟。
        run_id / round_no: 只在 ``outcome == "woke"`` 时有意义，指向那一轮决策。

    Returns:
        是否真的写到了一行（``False`` = 行不存在或已结过账）。
    """
    row = await session.get(AiIntelAlert, int(alert_id))
    if row is None or (row.outcome or ""):
        return False
    row.outcome = str(outcome or "")[:24]
    row.outcome_ts = int(outcome_ts if outcome_ts is not None else time.time())
    row.run_id = int(run_id or 0)
    row.round_no = int(round_no or 0)
    if commit:
        await session.commit()
    return True


async def alert_cooldown_state(session: AsyncSession, inst_id: str, *,
                               now: float | None = None) -> dict:
    """冷却时刻与当日已 fired 数（`plan_alert` 的两个入参，**从台账现算**）。

    两条走 `ix_ai_intel_alert_fired` 的点查，代价可忽略——换来的是没有第二份需要维护的账。

    Returns:
        ``{"lastFiredTs": int, "dayFired": int}``；从未告警过 ⇒ ``{0, 0}``。
    """
    wall = time.time() if now is None else float(now)
    last = (await session.execute(
        select(func.coalesce(func.max(AiIntelAlert.created_ts), 0)).where(
            AiIntelAlert.inst_id == inst_id, AiIntelAlert.fired.is_(True)))).scalar() or 0
    day_start = _utc_day_start(wall)
    day_fired = (await session.execute(
        select(func.count()).select_from(AiIntelAlert).where(
            AiIntelAlert.inst_id == inst_id, AiIntelAlert.fired.is_(True),
            AiIntelAlert.created_ts >= day_start))).scalar() or 0
    return {"lastFiredTs": int(last), "dayFired": int(day_fired)}


def _utc_day_start(now: float) -> int:
    """当前 UTC 日的起点（秒）。日配额跨日清零的判据与 `utc_day_key` **同源**。

    直接对 86400 取整会在闰秒 / 时区无关的意义上正确，但把它写成「由 `utc_day_key` 派生」是
    刻意的：两处各算一次「今天从几点开始」正是本仓库反复踩过的漂移源。
    """
    key = utc_day_key(now)
    return int(datetime.strptime(key, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


async def list_intel_alerts(session: AsyncSession, *, inst_id: str, limit: int = 50,
                            before: int | None = None) -> tuple[list[dict], bool]:
    """告警台账的游标分页（按 `id` 降序）。

    游标是 `id` 而不是时间戳：同一秒里的多行用时间戳翻页会漏行（沿用 `/citations` 的既有裁决）。

    Returns:
        ``(rows, has_more)``。
    """
    stmt = select(AiIntelAlert).where(AiIntelAlert.inst_id == inst_id)
    if before:
        stmt = stmt.where(AiIntelAlert.id < int(before))
    capped = max(1, int(limit))
    rows = (await session.execute(
        stmt.order_by(AiIntelAlert.id.desc()).limit(capped + 1))).scalars().all()
    return [intel_alert_out(row) for row in rows[:capped]], len(rows) > capped


async def count_recent_alerts(session: AsyncSession, inst_id: str, *, since_ts: int) -> dict:
    """近 N 秒的 fired 计数 + 最近一次 fired 时刻（`AiTradeIntelSummary` 的两个新键）。

    Returns:
        ``{"alerts24h": int, "lastAlertTs": int | None}``；`None` = 从未告警过（**不是 0**——
        「没告警过」与「1970 年告警过」是两句不同的话）。
    """
    fired = (await session.execute(
        select(func.count()).select_from(AiIntelAlert).where(
            AiIntelAlert.inst_id == inst_id, AiIntelAlert.fired.is_(True),
            AiIntelAlert.created_ts >= int(since_ts)))).scalar() or 0
    last = (await session.execute(
        select(func.coalesce(func.max(AiIntelAlert.created_ts), 0)).where(
            AiIntelAlert.inst_id == inst_id, AiIntelAlert.fired.is_(True)))).scalar() or 0
    return {"alerts24h": int(fired), "lastAlertTs": int(last) or None}


async def prune_intel_alerts(session: AsyncSession, *, before_ts: int,
                             commit: bool = True) -> int:
    """裁剪过期的台账行（挂在整理轮末尾，与 `prune_intel_citations` 同一处）。

    一个标的每天至多 `daily_cap` 条 fired + 每轮 1 条判定行，30 天上界约 1500 行——本函数是
    R-9 的兑现，不是热路径。

    Returns:
        删掉的行数。
    """
    rows = (await session.execute(
        select(AiIntelAlert.id).where(AiIntelAlert.created_ts < int(before_ts)))).all()
    ids = [int(row[0]) for row in rows]
    if not ids:
        return 0
    await session.execute(delete(AiIntelAlert).where(AiIntelAlert.id.in_(ids)))
    if commit:
        await session.commit()
    return len(ids)
