"""成果资产台账两张新表的数据访问（discovery-asset-ledger §5）。

只碰 ``discovery_assets`` / ``discovery_asset_checks``。**严禁**在本模块写
``factors`` / ``factor_sets`` / ``strategies`` / ``strategy_runs`` 的任何行（AL-6）——
体检守护是一个后台协程，它自动改用户正在用的实体是本项目能犯的最严重的错误。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import DiscoveryAsset, DiscoveryAssetCheck, DiscoveryCandidate, DiscoveryRun

SORT_COLUMNS = {
    "firstFound": DiscoveryAsset.first_found_ts,
    "lastFound": DiscoveryAsset.last_found_ts,
    "lastCheck": DiscoveryAsset.last_check_ts,
    "retention": DiscoveryAsset.retention,
    "acceptStrength": DiscoveryAsset.trials_at_accept,
}
HEALTH_STATUSES = ("fresh", "healthy", "watch", "decayed", "unknown", "error")


def dumps(value: object) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def loads(raw: str | None, fallback: object = None):
    """宽松解析：脏数据回落 ``fallback``，绝不让一行坏 JSON 打断整页列表。"""
    try:
        return json.loads(raw or "null") if raw else fallback
    except (json.JSONDecodeError, TypeError):
        return fallback


@dataclass
class AssetValues:
    """新建一条资产所需的全部可写字段（聚合传参，避免位置参数堆叠）。"""

    asset_key: str
    kind: str
    inst_id: str
    bar: str
    horizon: int
    direction: str
    expr_text: str
    genome: dict
    describe_text: str
    mechanism: str
    label: str
    found_ts: int
    source: dict
    mined_window: dict
    accept_metrics: dict
    accept_thresholds: dict
    trials_at_accept: int
    next_check_ts: int
    backfilled: bool = False


@dataclass
class CheckValues:
    """一次体检的落库形态（口径三个 ts 一并落库，AL-2 可回溯）。"""

    asset_id: int
    checked_ts: int
    window_start_ts: int
    window_end_ts: int
    fresh_start_ts: int
    fresh_bars: int
    warmup_bars: int
    status: str
    reasons: list = field(default_factory=list)
    retention: float | None = None
    clip_mode: str = ""
    metrics: dict = field(default_factory=dict)
    error: str = ""


# ============================ 资产 CRUD ============================
async def get_by_key(session: AsyncSession, asset_key: str) -> DiscoveryAsset | None:
    return (await session.execute(select(DiscoveryAsset).where(
        DiscoveryAsset.asset_key == asset_key))).scalars().first()


async def get_asset(session: AsyncSession, asset_id: int) -> DiscoveryAsset | None:
    return await session.get(DiscoveryAsset, asset_id)


async def count_assets(session: AsyncSession) -> int:
    return int((await session.execute(
        select(func.count()).select_from(DiscoveryAsset))).scalar() or 0)


async def create_asset(session: AsyncSession, values: AssetValues) -> DiscoveryAsset:
    """新建资产行（``asset_key`` 唯一索引即幂等的物理保证）。"""
    row = DiscoveryAsset(
        asset_key=values.asset_key[:48], kind=values.kind[:24], inst_id=values.inst_id[:32],
        bar=values.bar[:8], horizon=int(values.horizon), direction=values.direction[:8],
        expr_text=values.expr_text, genome_json=dumps(values.genome),
        describe_text=values.describe_text[:512], mechanism=values.mechanism[:48],
        label=values.label[:128], first_found_ts=int(values.found_ts),
        last_found_ts=int(values.found_ts), source_count=1,
        sources_json=dumps([values.source]), variant_count=1,
        backfilled=bool(values.backfilled), mined_window_json=dumps(values.mined_window),
        accept_metrics_json=dumps(values.accept_metrics),
        accept_thresholds_json=dumps(values.accept_thresholds),
        trials_at_accept=int(values.trials_at_accept), health_status="fresh",
        next_check_ts=int(values.next_check_ts))
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


def merge_sources(existing: list, incoming: dict, cap: int = 20) -> list:
    """溯源合并（AL-11）：超上限时保留**最早 1 条 + 最近 cap-1 条**。

    首次发现是溯源里最有价值的一条（``first_found_ts`` 的证据），绝不能被后来的挤掉。
    """
    merged = list(existing) + [incoming]
    if len(merged) <= cap:
        return merged
    return [merged[0]] + merged[-(cap - 1):]


async def merge_source(session: AsyncSession, row: DiscoveryAsset, *, found_ts: int,
                       source: dict) -> DiscoveryAsset:
    """已存在的资产**只做加法**：判级分母（指标 / 阈值 / 窗口 / 方向）一律不动（AL-11）。"""
    sources = merge_sources(loads(row.sources_json, []) or [], source)
    row.sources_json = dumps(sources)
    row.source_count = int(row.source_count or 0) + 1
    row.last_found_ts = max(int(row.last_found_ts or 0), int(found_ts))
    row.variant_count = len({str(item.get("genomeHash") or "") for item in sources}) or 1
    await session.commit()
    await session.refresh(row)
    return row


async def candidate_ids_by_hash(session: AsyncSession, run_id: int,
                                hashes: list[str]) -> dict[str, int]:
    """``genome_hash → candidate_id``（``uq_discovery_candidate`` 保证一一对应）。

    ``commit_iteration`` 不回候选 id（已核源码），登记侧自行反查一次即可，**不改**它的签名。
    """
    if not hashes:
        return {}
    rows = (await session.execute(select(
        DiscoveryCandidate.genome_hash, DiscoveryCandidate.id).where(
            DiscoveryCandidate.run_id == int(run_id),
            DiscoveryCandidate.genome_hash.in_(list(hashes))))).all()
    return {str(genome_hash): int(candidate_id) for genome_hash, candidate_id in rows}


async def existing_run_ids(session: AsyncSession, run_ids: list[int]) -> set[int]:
    """现存 run 的 id 集合（悬空溯源判定**一律由服务端算**，评审 P2-4）。"""
    if not run_ids:
        return set()
    rows = (await session.execute(select(DiscoveryRun.id).where(
        DiscoveryRun.id.in_(list(run_ids))))).scalars().all()
    return {int(value) for value in rows}


@dataclass(frozen=True)
class AssetQuery:
    """列表筛选（聚合传参：字段多且全可选，位置参数会立刻越过 5 个上限）。"""

    kind: str = ""
    status: str = ""
    promoted: str = ""          # "1" 只看已入库 / "0" 只看未入库 / "" 不过滤
    archived: str = "0"
    inst_id: str = ""
    bar: str = ""
    mechanism: str = ""
    q: str = ""
    sort: str = "lastFound"
    order: str = "desc"
    limit: int = 50
    offset: int = 0


def _apply_filters(stmt, query: AssetQuery):
    for column, value in ((DiscoveryAsset.kind, query.kind),
                          (DiscoveryAsset.health_status, query.status),
                          (DiscoveryAsset.inst_id, query.inst_id),
                          (DiscoveryAsset.bar, query.bar),
                          (DiscoveryAsset.mechanism, query.mechanism)):
        if value:
            stmt = stmt.where(column == value)
    if query.archived in ("0", "1"):
        stmt = stmt.where(DiscoveryAsset.archived == (query.archived == "1"))
    if query.promoted == "1":
        stmt = stmt.where(DiscoveryAsset.promoted_id.is_not(None))
    elif query.promoted == "0":
        stmt = stmt.where(DiscoveryAsset.promoted_id.is_(None))
    if query.q:
        like = f"%{query.q}%"
        stmt = stmt.where(DiscoveryAsset.describe_text.like(like)
                          | DiscoveryAsset.expr_text.like(like)
                          | DiscoveryAsset.label.like(like))
    return stmt


async def list_assets(session: AsyncSession,
                      query: AssetQuery) -> tuple[list[DiscoveryAsset], int]:
    """台账分页（总数走独立 count，绝不用 ``len(items)`` 冒充 total）。"""
    stmt = _apply_filters(select(DiscoveryAsset), query)
    count_stmt = _apply_filters(select(func.count()).select_from(DiscoveryAsset), query)
    column = SORT_COLUMNS.get(query.sort, DiscoveryAsset.last_found_ts)
    ordering = column.asc() if query.order == "asc" else column.desc()
    rows = list((await session.execute(
        stmt.order_by(ordering, DiscoveryAsset.id.desc())
        .limit(int(query.limit)).offset(int(query.offset)))).scalars().all())
    total = int((await session.execute(count_stmt)).scalar() or 0)
    return rows, total


async def list_due_assets(session: AsyncSession, *, now: int,
                          limit: int) -> list[DiscoveryAsset]:
    """到期资产（走 ``ix_discovery_assets_due``；已归档的一律不进体检队列，D-25）。"""
    return list((await session.execute(
        select(DiscoveryAsset).where(DiscoveryAsset.archived == False,   # noqa: E712
                                     DiscoveryAsset.next_check_ts <= int(now))
        .order_by(DiscoveryAsset.next_check_ts.asc(), DiscoveryAsset.id.asc())
        .limit(int(limit)))).scalars().all())


async def set_archived(session: AsyncSession, asset_id: int, *, archived: bool,
                       now: int) -> DiscoveryAsset | None:
    """归档 / 取消归档：**只**改这两列。``next_check_ts`` 保留原值（取消归档即恢复排队）。"""
    row = await session.get(DiscoveryAsset, asset_id)
    if row is None:
        return None
    row.archived = bool(archived)
    row.archived_ts = int(now) if archived else 0
    await session.commit()
    await session.refresh(row)
    return row


async def set_note(session: AsyncSession, asset_id: int, note: str) -> DiscoveryAsset | None:
    row = await session.get(DiscoveryAsset, asset_id)
    if row is None:
        return None
    row.note = (note or "")[:256]
    await session.commit()
    await session.refresh(row)
    return row


async def set_promoted(session: AsyncSession, asset_id: int, *, kind: str, entity_id: int,
                       companion_strategy_id: int | None) -> DiscoveryAsset | None:
    row = await session.get(DiscoveryAsset, asset_id)
    if row is None:
        return None
    row.promoted_kind = kind[:16]
    row.promoted_id = int(entity_id)
    row.companion_strategy_id = (None if companion_strategy_id is None
                                 else int(companion_strategy_id))
    await session.commit()
    await session.refresh(row)
    return row


# ============================ 体检记录 ============================
async def write_check(session: AsyncSession, values: CheckValues, *, health: dict,
                      max_rows: int) -> DiscoveryAssetCheck:
    """一次事务写「check 行 + 资产聚合列」，随后按保留上限剪最旧并累加 ``checks_pruned``。

    ``health`` 为聚合列补丁：``status`` / ``retention`` / ``consecutiveFail`` / ``nextCheckTs``。
    剪枝量如实累加——**绝不静默截断**（沿用上游保留纪律）。
    """
    row = DiscoveryAssetCheck(
        asset_id=int(values.asset_id), checked_ts=int(values.checked_ts),
        window_start_ts=int(values.window_start_ts),
        window_end_ts=int(values.window_end_ts), fresh_start_ts=int(values.fresh_start_ts),
        fresh_bars=int(values.fresh_bars), warmup_bars=int(values.warmup_bars),
        status=values.status[:16], reasons=",".join(values.reasons)[:64],
        retention=values.retention, clip_mode=values.clip_mode[:16],
        metrics_json=dumps(values.metrics), error=(values.error or "")[:256])
    session.add(row)
    asset = await session.get(DiscoveryAsset, int(values.asset_id))
    if asset is not None:
        asset.health_status = str(health.get("status") or values.status)[:16]
        asset.retention = health.get("retention")
        asset.consecutive_fail = int(health.get("consecutiveFail", 0))
        asset.last_reasons = ",".join(values.reasons)[:64]
        asset.last_check_ts = int(values.checked_ts)
        asset.next_check_ts = int(health.get("nextCheckTs", asset.next_check_ts))
        asset.checks_count = int(asset.checks_count or 0) + 1
        asset.checks_pruned = int(asset.checks_pruned or 0) + await _prune_checks(
            session, int(values.asset_id), int(max_rows))
    await session.commit()
    await session.refresh(row)
    return row


async def _prune_checks(session: AsyncSession, asset_id: int, cap: int) -> int:
    """每资产保留最近 ``cap`` 条（含本次刚 add 的那条，故先 flush）。"""
    await session.flush()
    total = int((await session.execute(
        select(func.count()).select_from(DiscoveryAssetCheck).where(
            DiscoveryAssetCheck.asset_id == asset_id))).scalar() or 0)
    excess = total - max(1, int(cap))
    if excess <= 0:
        return 0
    victims = (await session.execute(
        select(DiscoveryAssetCheck.id).where(DiscoveryAssetCheck.asset_id == asset_id)
        .order_by(DiscoveryAssetCheck.checked_ts.asc(), DiscoveryAssetCheck.id.asc())
        .limit(excess))).scalars().all()
    if victims:
        await session.execute(delete(DiscoveryAssetCheck).where(
            DiscoveryAssetCheck.id.in_(list(victims))))
    return len(victims)


async def list_checks(session: AsyncSession, asset_id: int, *, limit: int = 50,
                      offset: int = 0) -> tuple[list[DiscoveryAssetCheck], int]:
    """体检序列（倒序取、正序回，供折线图直接渲染）。"""
    rows = list((await session.execute(
        select(DiscoveryAssetCheck).where(DiscoveryAssetCheck.asset_id == int(asset_id))
        .order_by(DiscoveryAssetCheck.checked_ts.desc(), DiscoveryAssetCheck.id.desc())
        .limit(int(limit)).offset(int(offset)))).scalars().all())
    total = int((await session.execute(
        select(func.count()).select_from(DiscoveryAssetCheck).where(
            DiscoveryAssetCheck.asset_id == int(asset_id)))).scalar() or 0)
    return list(reversed(rows)), total


async def summary_counts(session: AsyncSession, *, now: int) -> dict:
    """台账摘要（§4.9 的非 guard 段；guard 段由守护自己的内存态拼上）。"""
    by_status = {status: 0 for status in HEALTH_STATUSES}
    for status, count in (await session.execute(
            select(DiscoveryAsset.health_status, func.count())
            .group_by(DiscoveryAsset.health_status))).all():
        by_status[str(status)] = int(count)
    return {
        "total": await count_assets(session),
        "byStatus": by_status,
        "promoted": await _count_where(session, DiscoveryAsset.promoted_id.is_not(None)),
        "archived": await _count_where(session, DiscoveryAsset.archived == True),  # noqa: E712
        "backfilled": await _count_where(session, DiscoveryAsset.backfilled == True),  # noqa: E712
        "checkedLast24h": int((await session.execute(
            select(func.count()).select_from(DiscoveryAssetCheck).where(
                DiscoveryAssetCheck.checked_ts >= int(now) - 86_400))).scalar() or 0),
        "newlyDecayedLast7d": int((await session.execute(
            select(func.count(func.distinct(DiscoveryAssetCheck.asset_id))).where(
                DiscoveryAssetCheck.status == "decayed",
                DiscoveryAssetCheck.checked_ts >= int(now) - 7 * 86_400))).scalar() or 0),
        "nextDueTs": int((await session.execute(
            select(func.min(DiscoveryAsset.next_check_ts)).where(
                DiscoveryAsset.archived == False))).scalar() or 0) or None,  # noqa: E712
    }


async def _count_where(session: AsyncSession, condition) -> int:
    return int((await session.execute(
        select(func.count()).select_from(DiscoveryAsset).where(condition))).scalar() or 0)
