"""影子意图表的数据层（ai-trade-evidence-gated-capital §3.6 / §6.1）。

与 `ai_evidence_models.py` 同一理由不进 `repositories.py`（混合文件）；调用方直接 import 本模块。

纪律：
1. 写入以 `shadow_key` 定位：同一笔影子从开到结束只有一行，重复写入是 UPDATE 不是第二行。
2. 回填以 `(source, source_ref)` 唯一：先查后插，重复点击幂等，不靠捕获 IntegrityError。
3. 读样本一律带 `LIMIT`（表只增不删，证据本身有价值，靠 `max_age_days` 过滤）。
"""
from __future__ import annotations

import time
from collections.abc import Iterable

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .ai_evidence_models import AiTradeShadowIntent

SOURCE_LIVE = "live"
SOURCE_BACKFILL = "backfill"

# `row_values` 产出、可被 UPDATE 覆盖的列（身份列 shadow_key / source / source_ref 不在内）。
_MUTABLE = ("run_id", "group_id", "round_no", "traded", "repeats", "bars_seen", "last_bar_ts",
            "mfe_bps", "mae_bps", "status", "exit_kind", "exit_px", "matured_wall", "gross_bps",
            "net_bps", "void_reason")


async def upsert_shadows(session: AsyncSession, rows: Iterable[dict], *,
                         commit: bool = True) -> int:
    """按 `shadow_key` 插入或更新（运行时 `persist_pending` 的唯一写路径）。

    Args:
        rows: `evidence_service.row_values` 的产物（列名即键）。
        commit: 由调用方统一提交时传 False。

    Returns:
        写入（插入 + 更新）的行数。
    """
    now = int(time.time())
    count = 0
    for values in rows:
        key = str(values["shadow_key"])
        existing = (await session.execute(select(AiTradeShadowIntent).where(
            AiTradeShadowIntent.shadow_key == key))).scalar_one_or_none()
        if existing is None:
            session.add(AiTradeShadowIntent(**values, created_ts=now, updated_ts=now))
        else:
            for name in _MUTABLE:
                if name in values:
                    setattr(existing, name, values[name])
            existing.updated_ts = now
        count += 1
    if commit:
        await session.commit()
    return count


async def insert_backfill(session: AsyncSession, values: dict) -> bool:
    """插入一行回填样本；`(source, source_ref)` 已存在 ⇒ False（幂等，不抛）。"""
    hit = (await session.execute(select(AiTradeShadowIntent.id).where(
        AiTradeShadowIntent.source == values["source"],
        AiTradeShadowIntent.source_ref == values["source_ref"]))).first()
    if hit is not None:
        return False
    now = int(time.time())
    session.add(AiTradeShadowIntent(**values, created_ts=now, updated_ts=now))
    await session.flush()
    return True


def _matured(bar: str, policy_version: str, side: str, since: float):
    return (AiTradeShadowIntent.bar == bar,
            AiTradeShadowIntent.policy_version == policy_version,
            AiTradeShadowIntent.side == side,
            AiTradeShadowIntent.status == "matured",
            AiTradeShadowIntent.matured_wall >= since,
            AiTradeShadowIntent.net_bps.is_not(None))


async def load_samples(session: AsyncSession, *, bar: str, policy_version: str, side: str,
                       since: float, limit: int, inst_id: str | None = None
                       ) -> list[tuple[float, float]]:
    """已结束样本 `(matured_wall, net_bps)` 升序；`inst_id=None` ⇒ 同方向跨标的合并样本。"""
    conds = list(_matured(bar, policy_version, side, since))
    if inst_id is not None:
        conds.append(AiTradeShadowIntent.inst_id == inst_id)
    stmt = (select(AiTradeShadowIntent.matured_wall, AiTradeShadowIntent.net_bps)
            .where(*conds).order_by(AiTradeShadowIntent.matured_wall.desc()).limit(int(limit)))
    rows = (await session.execute(stmt)).all()
    return sorted((float(w), float(v)) for w, v in rows)


async def load_bucket_rows(session: AsyncSession, *, inst_id: str, bar: str,
                           policy_version: str, side: str, since: float,
                           limit: int) -> list[AiTradeShadowIntent]:
    """端点预览用：最近 `limit` 笔已结束样本的整行（要按 `source` 单列回填数）。"""
    stmt = (select(AiTradeShadowIntent)
            .where(*_matured(bar, policy_version, side, since),
                   AiTradeShadowIntent.inst_id == inst_id)
            .order_by(AiTradeShadowIntent.matured_wall.desc()).limit(int(limit)))
    return list((await session.execute(stmt)).scalars().all())


async def count_open(session: AsyncSession, *, inst_id: str, bar: str, policy_version: str,
                     side: str) -> int:
    stmt = select(func.count(AiTradeShadowIntent.id)).where(
        AiTradeShadowIntent.inst_id == inst_id, AiTradeShadowIntent.bar == bar,
        AiTradeShadowIntent.policy_version == policy_version,
        AiTradeShadowIntent.side == side, AiTradeShadowIntent.status == "open")
    return int((await session.execute(stmt)).scalar_one() or 0)


async def load_open(session: AsyncSession, *, inst_id: str, bar: str,
                    policy_version: str) -> list[AiTradeShadowIntent]:
    """同 `(inst, bar, policy_version)` 的未结束影子（接管候选）。"""
    stmt = select(AiTradeShadowIntent).where(
        AiTradeShadowIntent.inst_id == inst_id, AiTradeShadowIntent.bar == bar,
        AiTradeShadowIntent.policy_version == policy_version,
        AiTradeShadowIntent.status == "open").order_by(AiTradeShadowIntent.opened_wall)
    return list((await session.execute(stmt)).scalars().all())


async def void_open_before(session: AsyncSession, *, inst_id: str, bar: str,
                           before_wall: float, reason: str = "gap") -> int:
    """过老的 open 影子一律作废（一条 UPDATE）：到现在必然早已越过时间离场点，路径已不可得。"""
    result = await session.execute(update(AiTradeShadowIntent).where(
        AiTradeShadowIntent.inst_id == inst_id, AiTradeShadowIntent.bar == bar,
        AiTradeShadowIntent.status == "open",
        AiTradeShadowIntent.opened_wall < before_wall).values(
        status="void", exit_kind="void", void_reason=reason, updated_ts=int(time.time())))
    return int(result.rowcount or 0)


async def runs_with_live_shadows(session: AsyncSession, run_ids: Iterable[int]) -> set[int]:
    """哪些 run 已有实时影子（回填据此整 run 跳过，评审 P1-3 ①）。"""
    ids = sorted({int(r) for r in run_ids})
    if not ids:
        return set()
    stmt = select(AiTradeShadowIntent.run_id).where(
        AiTradeShadowIntent.source == SOURCE_LIVE,
        AiTradeShadowIntent.run_id.in_(ids)).distinct()
    return {int(r) for (r,) in (await session.execute(stmt)).all()}


async def backfill_refs(session: AsyncSession, refs: Iterable[str]) -> set[str]:
    """已存在的回填 `source_ref` 集合（回执里的 duplicate 计数）。"""
    wanted = sorted(set(refs))
    if not wanted:
        return set()
    stmt = select(AiTradeShadowIntent.source_ref).where(
        AiTradeShadowIntent.source == SOURCE_BACKFILL,
        AiTradeShadowIntent.source_ref.in_(wanted))
    return {str(r) for (r,) in (await session.execute(stmt)).all()}


async def recent_live_counts(session: AsyncSession, *, inst_ids: list[str], bar: str,
                             since: float) -> dict[str, dict[str, int]]:
    """最近窗口里每个标的、每个方向的实时影子数（向导的「攒满要几天」估计）。"""
    if not inst_ids:
        return {}
    stmt = (select(AiTradeShadowIntent.inst_id, AiTradeShadowIntent.side,
                   func.count(AiTradeShadowIntent.id))
            .where(AiTradeShadowIntent.source == SOURCE_LIVE,
                   AiTradeShadowIntent.bar == bar,
                   AiTradeShadowIntent.inst_id.in_(inst_ids),
                   AiTradeShadowIntent.opened_wall >= since)
            .group_by(AiTradeShadowIntent.inst_id, AiTradeShadowIntent.side))
    out: dict[str, dict[str, int]] = {}
    for inst, side, n in (await session.execute(stmt)).all():
        out.setdefault(str(inst), {})[str(side)] = int(n)
    return out
