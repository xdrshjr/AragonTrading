"""标的市场情报层的数据层（ai-trade-market-intel §五）。

本模块**自包含**（沿用 `ai_auto_repositories.py` / `ai_experience_repositories.py` 的既有拆分
范式）：不 import 同仓在制模块，公开函数经 `repositories.py` re-export，调用点仍写 `repo.X`。

**`tier` 只有两个写点**（不变式 I-3，评审 P0-2）：`apply_curation`（整理轮）与 `patch_item`
（用户移层 / 编辑）。两者都在**同一条 UPDATE** 里同时写 `tier` 与调用方算好的 `expires_ts`，
不给「只改了一半」留缝——不重算的后果不是某条情报早退休了，而是整座多级库在事件发生 72 小时后
被确定性清理**整层扫空**，而 G-4「下次重开原样还在」对任何超过三天的条目恒假。
本模块以 `ValueError` 把「写了 tier 却没给 expires_ts」变成一次**构造期**失败，而不是一条静默
的错数据。

`upsert_items` 一律走 `sqlite_insert(...).on_conflict_do_update`：`uq_ai_intel_dedup` 跨全部
status，而闸⑤ 的查询作用域即便与它对齐，也仍存在「两轮并发」这种窄窗口——第二道保险的成本是
一行，代价是整轮事务回滚（评审 P0-3）。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AiIntelFeedback, AiIntelItem, AiIntelRound, AiIntelWatch

# `upsert_items` 冲突分支上**绝不覆盖**的列：用户意志（置顶 / 手改）与层级历史。
# 复活分支只把 `status` 拉回 `active` 并刷新来源与寿命，**保留原 tier 与 pinned**。
_PRESERVED_ON_CONFLICT = ("tier", "pinned", "user_edited", "promote_count", "demote_count",
                          "created_ts")


def _loads(raw: str | None, fallback):
    """JSON 列 → Python；缺 / 空 / 坏 JSON 一律回落（沿用既有 `run_out` 容错口径）。"""
    if not raw:
        return fallback
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return fallback
    return parsed


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def utc_day_key(ts: float | None = None) -> str:
    """UTC 日键（`"YYYY-MM-DD"`）。日配额跨日清零的唯一判据。"""
    moment = time.time() if ts is None else float(ts)
    return datetime.fromtimestamp(moment, tz=timezone.utc).strftime("%Y-%m-%d")


# ---------------- wire 映射（API 与服务层共用同一份口径） ----------------
def intel_item_out(row: AiIntelItem) -> dict:
    """一条情报 → camelCase wire（读端点与 WS 共用；**绝不**在别处再抄一份）。"""
    return {
        "id": row.id,
        "instId": row.inst_id,
        "tier": row.tier,
        "category": row.category,
        "headline": row.headline,
        "summary": row.summary,
        "stance": row.stance,
        "impact": row.impact,
        "confidence": round(float(row.confidence or 0.0), 4),
        "eventTs": int(row.event_ts or 0),
        "capturedTs": int(row.captured_ts or 0),
        "updatedTs": int(row.updated_ts or 0),
        "expiresTs": int(row.expires_ts or 0),
        "status": row.status,
        "pinned": bool(row.pinned),
        "userEdited": bool(row.user_edited),
        "undated": bool(row.undated),
        "isScheduled": bool(row.is_scheduled),
        "origin": row.origin,
        "sources": _loads(row.sources_json, []),
        "sourceCount": int(row.source_count or 0),
        "domainCount": int(row.domain_count or 0),
        "dedupKey": row.dedup_key,
        "supersedesId": row.supersedes_id,
        "promoteCount": int(row.promote_count or 0),
        "demoteCount": int(row.demote_count or 0),
        "lastCuratedTs": int(row.last_curated_ts or 0),
        "curationNote": row.curation_note or "",
        "createdTs": int(row.created_ts or 0),
        # ---- closed-loop §5.2 的十个新键（E-16：本函数是条目 wire 的**唯一**装配点，
        # `apps/web/lib/intelTypes.ts` 与它逐键同构，改这里必须同时改那里）。
        # `getattr` 兜底是给「补列 patcher 尚未跑过」的极早期读留的安全网，不是常态路径。
        "citeCount": int(getattr(row, "cite_count", 0) or 0),
        "citeEntryCount": int(getattr(row, "cite_entry_count", 0) or 0),
        "settledCount": int(getattr(row, "settled_count", 0) or 0),
        "winCount": int(getattr(row, "win_count", 0) or 0),
        "netPnlUsd": round(float(getattr(row, "net_pnl_usd", 0.0) or 0.0), 6),
        "lastCitedTs": int(getattr(row, "last_cited_ts", 0) or 0),
        "verdict": getattr(row, "verdict", "") or "",
        "verdictTs": int(getattr(row, "verdict_ts", 0) or 0),
        "verdictNote": getattr(row, "verdict_note", "") or "",
        "verifyCount": int(getattr(row, "verify_count", 0) or 0),
        # ---- ai-trade-intel-evidence-grounding §5.3.1 R-a：**读出通路的第一跳** ----
        # 漏了这四个键，前端恒收不到 ⇒ 卡片恒渲染「无引证」、轴 C 的入口**永远不出现**；而只断言
        # 「库里有引文行」的端到端用例照样全绿。`quoteState` 必须由后端下发（前端手里没有任何
        # 能区分「模式关 / 没抓到正文 / 模型没给 / 给了没比中」的信息）。
        "quoted": bool(getattr(row, "quoted", False)),
        "quoteCount": int(getattr(row, "quote_count", 0) or 0),
        "quoteState": getattr(row, "quote_state", "") or "",
        "scheduleResolvedTs": int(getattr(row, "schedule_resolved_ts", 0) or 0),
    }


def intel_round_out(row: AiIntelRound) -> dict:
    """一轮审计存根 → camelCase wire。"""
    return {
        "id": row.id,
        "instId": row.inst_id,
        "kind": row.kind,
        "groupId": row.group_id,
        "startedTs": int(row.started_ts or 0),
        "finishedTs": int(row.finished_ts or 0),
        "status": row.status,
        "searchProvider": row.search_provider or "",
        "queries": _loads(row.queries_json, []),
        "docCount": int(row.doc_count or 0),
        "domainCount": int(row.domain_count or 0),
        "accepted": int(row.accepted or 0),
        "rejected": int(row.rejected or 0),
        "merged": int(row.merged or 0),
        "rejects": _loads(row.rejects_json, {}),
        "actions": _loads(row.actions_json, {}),
        "inputTokens": int(row.llm_input_tokens or 0),
        "outputTokens": int(row.llm_output_tokens or 0),
        "degradeCode": row.degrade_code or "",
        "errorText": row.error_text or "",
        # ---- ai-trade-intel-evidence-grounding §5.3.1 R-d：round wire 的**唯一**装配点 ----
        # 漏了这四个键，轮次页签的两列恒 `0/0`、命中率徽标恒 0 ⇒ 把 `quote_mode` 翻到 strict 的
        # 运维判据**永远达不成**，而它是 R-4 唯一的缓解手段。
        "readerAttempt": int(getattr(row, "reader_attempt", 0) or 0),
        "readerOk": int(getattr(row, "reader_ok", 0) or 0),
        "quotesProposed": int(getattr(row, "quotes_proposed", 0) or 0),
        "quotesVerified": int(getattr(row, "quotes_verified", 0) or 0),
    }


def intel_watch_out(row: AiIntelWatch) -> dict:
    """采集节律与健康 → camelCase wire。"""
    return {
        "instId": row.inst_id,
        "enabled": bool(row.enabled),
        "lastCollectTs": int(row.last_collect_ts or 0),
        "lastCurateTs": int(row.last_curate_ts or 0),
        "nextDueTs": int(row.next_due_ts or 0),
        "lastSeenTs": int(row.last_seen_ts or 0),
        "collectsSinceCurate": int(row.collects_since_curate or 0),
        "failStreak": int(row.fail_streak or 0),
        "cooldownUntilTs": int(row.cooldown_until_ts or 0),
        "lastError": row.last_error or "",
        "dayKey": row.day_key or "",
        "dayRounds": int(row.day_rounds or 0),
        "updatedTs": int(row.updated_ts or 0),
        # ---- closed-loop §5.3 的两个新键 ----
        "lastVerifyTs": int(getattr(row, "last_verify_ts", 0) or 0),
        "curatesSinceVerify": int(getattr(row, "curates_since_verify", 0) or 0),
    }


# ---------------- watch ----------------
async def load_intel_watch(session: AsyncSession, inst_id: str) -> dict | None:
    """读一行 watch；不存在回 ``None``（调用方决定是否 `ensure`）。"""
    row = await session.get(AiIntelWatch, inst_id)
    return intel_watch_out(row) if row is not None else None


async def ensure_intel_watch(session: AsyncSession, inst_id: str, *, next_due_ts: int = 0,
                             commit: bool = True) -> dict:
    """取一行 watch，缺则按当前时刻建（幂等）。"""
    row = await session.get(AiIntelWatch, inst_id)
    now = int(time.time())
    if row is None:
        row = AiIntelWatch(inst_id=inst_id, enabled=True, next_due_ts=int(next_due_ts or now),
                           day_key=utc_day_key(now), updated_ts=now)
        session.add(row)
        if commit:
            await session.commit()
        await session.refresh(row)
    return intel_watch_out(row)


async def bump_intel_watch(session: AsyncSession, inst_id: str, *, kind: str, status: str,
                           next_due_ts: int, cooldown_until_ts: int = 0,
                           last_error: str = "", curate_reset: bool = False,
                           verify_reset: bool = False, commit: bool = True) -> dict:
    """一轮结束后推进节律与健康计数。

    `fail_streak` 的三态语义（§3.5.2）：`ok` 清零；`degraded` **不增不减**（拿到了东西但没过闸，
    不是链路故障）；`failed` +1。把 degraded 也算成失败，会让「模型今天比较保守」被当成链路故障
    一路退避到一小时一轮。

    ⚠️ **kind 分支必须显式三分**（closed-loop 评审 P0-3）。合入前它是一个二值 catch-all
    （`if kind == "collect": … else: row.last_curate_ts = now`），第三种 kind 会静默落进 `else`：

    1. 核查轮会改写整理轮的节律真值（`last_curate_ts` 被当成「刚整理过」），弹窗上的「上次整理」
       从此指着一个从没整理过的时刻；
    2. `curates_since_verify` 会**没有任何自增点**，恒为 0 ⇒ `_verify_due` 的
       `0 + 1 >= verify_every_n_curates` 只有默认值 1 才为真 —— 用户把这个 `ge=1, le=12` 的设置
       调到 2，核查轮从此**一次都不会跑**，界面上也不会有任何异样。

    未知 `kind` 一律**只推公共计数**（`day_rounds` / `fail_streak` / `next_due_ts`）而不碰任何
    节律字段——一个不认识的轮种不该有能力改写别人的节律。

    Args:
        kind: `collect` / `curate` / `verify`（其余值只推公共计数）。
        curate_reset: 整理轮跑完 ⇒ 清 `collects_since_curate`。
        verify_reset: 核查轮跑完 ⇒ 清 `curates_since_verify`（closed-loop §3.3.5 步骤 6）。
    """
    row = await session.get(AiIntelWatch, inst_id)
    now = int(time.time())
    if row is None:
        row = AiIntelWatch(inst_id=inst_id, day_key=utc_day_key(now))
        session.add(row)
    day = utc_day_key(now)
    if row.day_key != day:
        row.day_key = day
        row.day_rounds = 0
    row.day_rounds = int(row.day_rounds or 0) + 1
    if kind == "collect":
        row.last_collect_ts = now if status == "ok" else int(row.last_collect_ts or 0)
        row.collects_since_curate = int(row.collects_since_curate or 0) + 1
    elif kind == "curate":
        row.last_curate_ts = now
        # ★ 核查节律的**唯一**自增点（closed-loop 评审 P0-3）。
        row.curates_since_verify = int(row.curates_since_verify or 0) + 1
    elif kind == "verify":
        row.last_verify_ts = now        # **绝不动 last_curate_ts**（G-16）
    if curate_reset:
        row.collects_since_curate = 0
    if verify_reset:
        row.curates_since_verify = 0
    if status == "ok":
        row.fail_streak = 0
    elif status == "failed":
        row.fail_streak = int(row.fail_streak or 0) + 1
    row.next_due_ts = int(next_due_ts)
    row.cooldown_until_ts = int(cooldown_until_ts or 0)
    row.last_error = (last_error or "")[:200]
    row.updated_ts = now
    if commit:
        await session.commit()
    return intel_watch_out(row)


async def mark_intel_seen(session: AsyncSession, inst_id: str, *, ts: int | None = None,
                          commit: bool = True) -> dict:
    """把「用户看过了」落库（未读徽标的**唯一**真值写点，评审 P1-2）。"""
    row = await session.get(AiIntelWatch, inst_id)
    now = int(time.time())
    seen = int(ts if ts is not None else now)
    if row is None:
        row = AiIntelWatch(inst_id=inst_id, day_key=utc_day_key(now))
        session.add(row)
    row.last_seen_ts = seen
    row.updated_ts = now
    if commit:
        await session.commit()
    return intel_watch_out(row)


async def set_intel_watch_enabled(session: AsyncSession, inst_id: str, *, enabled: bool,
                                  commit: bool = True) -> dict:
    """开关某标的的自动采集（不影响已有条目的读与 CRUD）。"""
    row = await session.get(AiIntelWatch, inst_id)
    now = int(time.time())
    if row is None:
        row = AiIntelWatch(inst_id=inst_id, day_key=utc_day_key(now))
        session.add(row)
    row.enabled = bool(enabled)
    row.updated_ts = now
    if commit:
        await session.commit()
    return intel_watch_out(row)


# ---------------- items（读） ----------------
async def list_intel_items(session: AsyncSession, inst_id: str, *, tier: str | None = None,
                           category: str | None = None, status: str | None = "active",
                           q: str | None = None, limit: int = 50,
                           offset: int = 0) -> tuple[list[dict], int]:
    """按筛选取条目 + 总数（分页由调用方给）。排序：置顶优先 → 事件时刻降序。"""
    stmt = select(AiIntelItem).where(AiIntelItem.inst_id == inst_id)
    count_stmt = select(func.count()).select_from(AiIntelItem).where(
        AiIntelItem.inst_id == inst_id)
    for clause in _item_filters(tier, category, status, q):
        stmt = stmt.where(clause)
        count_stmt = count_stmt.where(clause)
    total = int((await session.execute(count_stmt)).scalar() or 0)
    stmt = stmt.order_by(AiIntelItem.pinned.desc(), AiIntelItem.event_ts.desc(),
                         AiIntelItem.id.desc()).offset(max(0, offset)).limit(max(1, limit))
    rows = (await session.execute(stmt)).scalars().all()
    return [intel_item_out(row) for row in rows], total


def _item_filters(tier: str | None, category: str | None, status: str | None, q: str | None):
    """筛选子句（列表与计数**共用**，故同一筛选下的条数在结构上不可能不一致）。"""
    clauses = []
    if tier:
        clauses.append(AiIntelItem.tier == tier)
    if category:
        clauses.append(AiIntelItem.category == category)
    if status:
        clauses.append(AiIntelItem.status == status)
    if q:
        needle = f"%{q.strip()}%"
        clauses.append(AiIntelItem.headline.like(needle) | AiIntelItem.summary.like(needle))
    return clauses


async def load_intel_scope(session: AsyncSession, inst_id: str,
                           statuses: tuple[str, ...] | None = None) -> list[dict]:
    """取该标的的条目全集（默认全部 status）。

    **闸⑤ 与整理轮都用它**：作用域必须与 `uq_ai_intel_dedup` 一致（跨全部 status），否则一条被
    TTL 归档的旧闻在被再次报道时会绕过去重、直接撞唯一约束，整轮事务回滚（评审 P0-3）。
    """
    stmt = select(AiIntelItem).where(AiIntelItem.inst_id == inst_id)
    if statuses:
        stmt = stmt.where(AiIntelItem.status.in_(statuses))
    rows = (await session.execute(stmt.order_by(AiIntelItem.id.asc()))).scalars().all()
    return [intel_item_out(row) for row in rows]


async def count_intel_by_tier(session: AsyncSession, inst_id: str) -> dict:
    """三层 active 计数 + 已归档 / 已静音计数（弹窗页签徽标的真值）。

    归档与静音分开计：两者的**成因**不同（前者是自动清理、后者是用户手动），而弹窗把它们合并到
    同一个抽屉里展示——合并在展示层做，不在数据层做。
    """
    stmt = (select(AiIntelItem.tier, AiIntelItem.status, func.count())
            .where(AiIntelItem.inst_id == inst_id)
            .group_by(AiIntelItem.tier, AiIntelItem.status))
    counts = {"ultra": 0, "mid": 0, "long": 0, "archived": 0, "muted": 0}
    for tier, status, n in (await session.execute(stmt)).all():
        if status == "active" and tier in counts:
            counts[tier] += int(n)
        elif status == "archived":
            counts["archived"] += int(n)
        elif status == "muted":
            counts["muted"] += int(n)
    return counts


async def count_intel_unread(session: AsyncSession, inst_id: str, *, last_seen_ts: int) -> int:
    """`captured_ts > last_seen_ts` 的 active 条数（**服务端算**，评审 P1-2）。"""
    stmt = (select(func.count()).select_from(AiIntelItem)
            .where(AiIntelItem.inst_id == inst_id, AiIntelItem.status == "active",
                   AiIntelItem.captured_ts > int(last_seen_ts or 0)))
    return int((await session.execute(stmt)).scalar() or 0)


async def get_intel_item(session: AsyncSession, item_id: int) -> dict | None:
    row = await session.get(AiIntelItem, int(item_id))
    return intel_item_out(row) if row is not None else None


# ---------------- items（写） ----------------
def _item_values(payload: dict, *, now: int) -> dict:
    """入库列值（**唯一**的字段搬运点；漏一列的症状是静默丢数据）。"""
    sources = payload.get("sources") or []
    return {
        "inst_id": payload["instId"],
        "tier": payload.get("tier", "ultra"),
        "category": payload["category"],
        "headline": payload["headline"],
        "summary": payload["summary"],
        "stance": payload.get("stance", "neutral"),
        "impact": payload.get("impact", "medium"),
        "confidence": float(payload.get("confidence") or 0.0),
        "event_ts": int(payload.get("eventTs") or 0),
        "captured_ts": int(payload.get("capturedTs") or now),
        "updated_ts": now,
        "expires_ts": int(payload.get("expiresTs") or 0),
        "status": payload.get("status", "active"),
        "pinned": bool(payload.get("pinned")),
        "user_edited": bool(payload.get("userEdited")),
        "undated": bool(payload.get("undated")),
        "is_scheduled": bool(payload.get("isScheduled")),
        "origin": payload.get("origin", "agent"),
        "origin_group_id": payload.get("originGroupId", ""),
        "origin_round_id": int(payload.get("originRoundId") or 0),
        "sources_json": _dumps(sources),
        "source_count": len(sources),
        "domain_count": int(payload.get("domainCount") or 0),
        "dedup_key": payload["dedupKey"],
        "created_ts": now,
    }


async def upsert_intel_items(session: AsyncSession, rows: list[dict], *,
                             commit: bool = True) -> list[int]:
    """批量落新条目（`ON CONFLICT(inst_id, dedup_key) DO UPDATE`）。

    冲突分支**只刷新来源与寿命**，`tier` / `pinned` / `user_edited` / 升降级计数一律保留——闸⑤
    的复活分支就是靠它做到「回 active、并来源、重算 expires_ts、**保留原 tier 与 pinned**」。

    Returns:
        实际写入 / 更新的条目 id 列表（顺序与入参一致）。
    """
    if not rows:
        return []
    now = int(time.time())
    ids: list[int] = []
    for payload in rows:
        values = _item_values(payload, now=now)
        update_cols = {k: v for k, v in values.items() if k not in _PRESERVED_ON_CONFLICT}
        stmt = (sqlite_insert(AiIntelItem).values(**values)
                .on_conflict_do_update(index_elements=["inst_id", "dedup_key"],
                                       set_=update_cols)
                .returning(AiIntelItem.id))
        ids.append(int((await session.execute(stmt)).scalar_one()))
    if commit:
        await session.commit()
    return ids


async def merge_intel_sources(session: AsyncSession, item_id: int, *, sources: list[dict],
                              domain_count: int, expires_ts: int, revive: bool,
                              updated_ts: int, commit: bool = True) -> None:
    """闸⑤ 的合并 / 复活分支：并来源、重算域名数与寿命；复活时把 status 拉回 `active`。

    **不改 tier、不改 pinned**——一条被用户升到长期又被 TTL 归档的情报，再次被报道时应该回到
    长期，而不是被打回超短。
    """
    values = {"sources_json": _dumps(sources), "source_count": len(sources),
              "domain_count": int(domain_count), "expires_ts": int(expires_ts),
              "updated_ts": int(updated_ts)}
    if revive:
        values["status"] = "active"
    await session.execute(update(AiIntelItem).where(AiIntelItem.id == int(item_id))
                          .values(**values))
    if commit:
        await session.commit()


async def patch_intel_item(session: AsyncSession, item_id: int, patch: dict, *,
                           expires_ts: int | None = None, user_edited: bool = True,
                           commit: bool = True) -> dict | None:
    """用户侧的编辑 / 移层 / 置顶 / 静音（`tier` 的第二个写点）。

    Args:
        item_id: 目标条目。
        patch: camelCase 的部分字段（`tier` / `category` / `headline` / `summary` / `stance` /
            `impact` / `pinned` / `status`）。
        expires_ts: 当 `patch` 含 `tier` **或** `pinned` 时**必须**给出重算后的寿命（不变式 I-3）。
        user_edited: 是否把这次改动记成「用户手改过」（第二把免死金牌）。默认 True；
            系统内部的状态迁移（如整理轮的 merge 收尾）显式传 False。

    Returns:
        更新后的 wire；条目不存在回 ``None``。

    Raises:
        ValueError: 写了 `tier` / `pinned` 却没给 `expires_ts`——这条不变式只能在构造期挡下来，
            运行期它是完全静默的（评审 P0-2）。
    """
    touches_lifetime = "tier" in patch or "pinned" in patch
    if touches_lifetime and expires_ts is None:
        raise ValueError("patch_intel_item: 写 tier / pinned 必须同时给出重算后的 expires_ts"
                         "（不变式 I-3；不重算会让整座多级库在超短 TTL 之后被整层扫空）")
    row = await session.get(AiIntelItem, int(item_id))
    if row is None:
        return None
    mapping = {"tier": "tier", "category": "category", "headline": "headline",
               "summary": "summary", "stance": "stance", "impact": "impact",
               "pinned": "pinned", "status": "status"}
    for wire_key, column in mapping.items():
        if wire_key in patch and patch[wire_key] is not None:
            setattr(row, column, patch[wire_key])
    if expires_ts is not None:
        row.expires_ts = int(expires_ts)
    if user_edited:
        row.user_edited = True
    row.updated_ts = int(time.time())
    if commit:
        await session.commit()
    return intel_item_out(row)


async def delete_intel_item(session: AsyncSession, item_id: int, *,
                            commit: bool = True) -> bool:
    """物理删除（**唯一**的硬删路径；自动清理一律走软删 `archived`）。

    ai-trade-intel-evidence-grounding §3.3.1：**删条目必删引文**，且在**同一个事务**里。
    本仓库的 SQLite 表一律不建外键，少了这一句会留下一堆没有宿主的证据行——它们在任何界面上
    都不可见，只会让 `ai_intel_quotes` 无限长。
    """
    from .ai_intel_evidence_repositories import delete_item_quotes   # 同层复用，避免第二份口径

    row = await session.get(AiIntelItem, int(item_id))
    if row is None:
        return False
    await delete_item_quotes(session, int(item_id), commit=False)
    await session.delete(row)
    if commit:
        await session.commit()
    return True


async def apply_intel_curation(session: AsyncSession, actions: list[dict], *,
                               now: int | None = None, commit: bool = True) -> dict:
    """执行整理轮**已过复核**的动作（`tier` 的第一个写点）。

    Args:
        actions: 形如 ``{"itemId", "action", "toTier", "mergeIntoId", "reason", "expiresTs"}``。
        now: 墙钟秒。
        commit: 由编排层统一提交时传 False。

    Returns:
        动作直方图（实际执行了几个）。

    Raises:
        ValueError: `promote` / `demote` 缺 `expiresTs`（不变式 I-3 的第一个写点）。
    """
    wall = int(time.time() if now is None else now)
    counts: dict[str, int] = {}
    for action in actions or []:
        kind = str(action.get("action") or "")
        row = await session.get(AiIntelItem, int(action.get("itemId") or 0))
        if row is None:
            continue
        if kind in ("promote", "demote"):
            if action.get("expiresTs") is None:
                raise ValueError("apply_intel_curation: promote / demote 必须携带重算后的 "
                                 "expiresTs（不变式 I-3）")
            row.tier = str(action["toTier"])
            row.expires_ts = int(action["expiresTs"])
            if kind == "promote":
                row.promote_count = int(row.promote_count or 0) + 1
            else:
                row.demote_count = int(row.demote_count or 0) + 1
        elif kind == "archive":
            row.status = "archived"
        elif kind == "merge":
            row.status = "superseded"
            row.supersedes_id = int(action.get("mergeIntoId") or 0) or None
        else:
            continue
        row.curation_note = str(action.get("reason") or "")[:200]
        row.last_curated_ts = wall
        row.updated_ts = wall
        counts[kind] = counts.get(kind, 0) + 1
    if commit:
        await session.commit()
    return counts


async def archive_intel_items(session: AsyncSession, item_ids: list[int], *, reason: str,
                              now: int | None = None, commit: bool = True) -> int:
    """确定性清理的软删（TTL / 溢出 / 孤儿 superseded 共用）。

    **软删而不是硬删**：归档行仍参与 `uq_ai_intel_dedup`（闸⑤ 的复活分支正是靠它），且 UI 的
    「已归档」筛选能看到它们——用户要能回答「那条情报为什么不见了」。
    """
    ids = [int(i) for i in item_ids if i]
    if not ids:
        return 0
    wall = int(time.time() if now is None else now)
    await session.execute(update(AiIntelItem).where(AiIntelItem.id.in_(ids))
                          .values(status="archived", curation_note=reason[:200],
                                  last_curated_ts=wall, updated_ts=wall))
    if commit:
        await session.commit()
    return len(ids)


async def purge_orphan_superseded(session: AsyncSession, inst_id: str, *,
                                  commit: bool = True) -> int:
    """孤儿 `superseded` 清理：指向的主条目已被硬删 ⇒ 一并归档（§3.4.4 第 3 步）。"""
    live_ids = set((await session.execute(
        select(AiIntelItem.id).where(AiIntelItem.inst_id == inst_id))).scalars().all())
    rows = (await session.execute(
        select(AiIntelItem).where(AiIntelItem.inst_id == inst_id,
                                  AiIntelItem.status == "superseded"))).scalars().all()
    orphans = [row.id for row in rows
               if row.supersedes_id and row.supersedes_id not in live_ids]
    return await archive_intel_items(session, orphans, reason="orphan_superseded",
                                     commit=commit)


# ---------------- rounds ----------------
async def write_intel_round(session: AsyncSession, payload: dict, *,
                            commit: bool = True) -> int:
    """落一轮审计存根（append-only）。返回 round id。"""
    row = AiIntelRound(
        inst_id=payload.get("instId", ""), kind=payload.get("kind", "collect"),
        group_id=payload.get("groupId", ""),
        started_ts=int(payload.get("startedTs") or 0),
        finished_ts=int(payload.get("finishedTs") or 0),
        status=payload.get("status", "ok"),
        search_provider=payload.get("searchProvider", ""),
        queries_json=_dumps(payload.get("queries") or []),
        doc_count=int(payload.get("docCount") or 0),
        domain_count=int(payload.get("domainCount") or 0),
        accepted=int(payload.get("accepted") or 0),
        rejected=int(payload.get("rejected") or 0),
        merged=int(payload.get("merged") or 0),
        rejects_json=_dumps(payload.get("rejects") or {}),
        actions_json=_dumps(payload.get("actions") or {}),
        llm_input_tokens=int(payload.get("inputTokens") or 0),
        llm_output_tokens=int(payload.get("outputTokens") or 0),
        degrade_code=(payload.get("degradeCode") or "")[:24],
        error_text=(payload.get("errorText") or "")[:400],
        # ---- ai-trade-intel-evidence-grounding §5.3：四个抓取 / 引文计数 ----
        # 本函数是**逐列手抄**的 payload 映射：新列不在这里加一行赋值，落库恒为 0 且不报错
        # （`append_live_trade` 逐列手抄的同形坑）。读点在 `intel_round_out`。
        reader_attempt=int(payload.get("readerAttempt") or 0),
        reader_ok=int(payload.get("readerOk") or 0),
        quotes_proposed=int(payload.get("quotesProposed") or 0),
        quotes_verified=int(payload.get("quotesVerified") or 0))
    session.add(row)
    if commit:
        await session.commit()
        await session.refresh(row)
    else:
        await session.flush()
    return int(row.id)


async def list_intel_rounds(session: AsyncSession, inst_id: str, *, kind: str | None = None,
                            limit: int = 20) -> list[dict]:
    """最近 N 轮审计存根（时间倒序）。"""
    stmt = select(AiIntelRound).where(AiIntelRound.inst_id == inst_id)
    if kind:
        stmt = stmt.where(AiIntelRound.kind == kind)
    rows = (await session.execute(
        stmt.order_by(AiIntelRound.started_ts.desc(), AiIntelRound.id.desc())
        .limit(max(1, limit)))).scalars().all()
    return [intel_round_out(row) for row in rows]


async def count_intel_rounds_today(session: AsyncSession, inst_id: str) -> int:
    """当前 UTC 日已完成的轮数（日配额的**校验**口径，与 watch 的计数互为对照）。"""
    day_start = int(datetime.strptime(utc_day_key(), "%Y-%m-%d")
                    .replace(tzinfo=timezone.utc).timestamp())
    stmt = (select(func.count()).select_from(AiIntelRound)
            .where(AiIntelRound.inst_id == inst_id, AiIntelRound.started_ts >= day_start))
    return int((await session.execute(stmt)).scalar() or 0)


# ---------------- feedback ----------------
async def list_intel_blocked_domains(session: AsyncSession, inst_id: str = "") -> list[str]:
    """用户标记「不再采纳此源」的域名（全局 + 该标的）。"""
    stmt = select(AiIntelFeedback.key).where(AiIntelFeedback.action == "block")
    if inst_id:
        stmt = stmt.where(AiIntelFeedback.inst_id.in_(("", inst_id)))
    else:
        stmt = stmt.where(AiIntelFeedback.inst_id == "")
    return [row for row in (await session.execute(stmt)).scalars().all() if row]


async def list_intel_trusted_domains(session: AsyncSession, inst_id: str = "") -> list[str]:
    """用户标记「更信任此源」的域名（全局 + 该标的）。

    与 `list_intel_blocked_domains` **逐字同构**，只把 `action == "block"` 换成 `"trust"`
    （closed-loop 评审 P1-7）。没有它，`trust` 就是第二个「有写点没读点」——用户点下的
    「更信任这个源」会变成一次什么都没发生的点击，而这件事不会报任何错。
    """
    stmt = select(AiIntelFeedback.key).where(AiIntelFeedback.action == "trust")
    if inst_id:
        stmt = stmt.where(AiIntelFeedback.inst_id.in_(("", inst_id)))
    else:
        stmt = stmt.where(AiIntelFeedback.inst_id == "")
    return [row for row in (await session.execute(stmt)).scalars().all() if row]


async def list_intel_feedback(session: AsyncSession, inst_id: str = "") -> list[dict]:
    """来源取舍清单（`GET /intel/feedback`）。`inst_id` 缺省 ⇒ 全部（全局 + 每个标的）。"""
    stmt = select(AiIntelFeedback)
    if inst_id:
        stmt = stmt.where(AiIntelFeedback.inst_id.in_(("", inst_id)))
    rows = (await session.execute(
        stmt.order_by(AiIntelFeedback.created_ts.desc(),
                      AiIntelFeedback.id.desc()))).scalars().all()
    return [{"id": row.id, "scope": row.scope, "key": row.key, "action": row.action,
             "instId": row.inst_id or "", "createdTs": int(row.created_ts or 0)}
            for row in rows]


async def set_intel_feedback(session: AsyncSession, *, key: str, action: str,
                             inst_id: str = "", commit: bool = True) -> None:
    """记一条来源取舍（幂等 upsert）。"""
    stmt = (sqlite_insert(AiIntelFeedback)
            .values(scope="domain", key=key, action=action, inst_id=inst_id,
                    created_ts=int(time.time()))
            .on_conflict_do_update(index_elements=["scope", "key", "inst_id"],
                                   set_={"action": action}))
    await session.execute(stmt)
    if commit:
        await session.commit()


async def clear_intel_feedback(session: AsyncSession, *, key: str, inst_id: str = "",
                               commit: bool = True) -> None:
    """撤销一条来源取舍。"""
    await session.execute(delete(AiIntelFeedback).where(
        AiIntelFeedback.scope == "domain", AiIntelFeedback.key == key,
        AiIntelFeedback.inst_id == inst_id))
    if commit:
        await session.commit()
