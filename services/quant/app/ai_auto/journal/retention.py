"""保留策略（ai-trade-audit-journal §2.3）。

双闸（天数 + 行数）**取并集**后按最老优先删；两个都为 0 = 永不剪枝。

**绝对禁止删除的行**：`group_id` 属于当前 `status ∈ {running, paused}` 的分组。判据取三处的
**并集**——库侧 run 状态、`ai_trade_group_states` 里的 `paused` 契约、以及内存 manager 手上的
那个分组。宁可少删：正在跑的会话的日志被删掉，是审计系统能犯的最严重的错。

触发点只有两处：① 应用启动后 30s 错峰一次（`main._prune_journal_later`）；
② 管理台的 `POST /journal/prune`（支持 `dryRun`）。**刻意没有常驻定时器**——多一个常驻协程
就多一个关停步骤，而日志的增长是缓慢的。
"""
from __future__ import annotations

import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from ...config import Settings, get_settings
from ...db import ai_journal_repositories as journal_repo

logger = logging.getLogger(__name__)

DAY_SECONDS = 86400


async def collect_protected_groups(session: AsyncSession) -> list[str]:
    """绝不删除其日志的分组 id（三处真源取**并集**，宁可少删）。"""
    protected: set[str] = set()
    try:
        protected.update(await journal_repo.protected_group_ids(session))
    except Exception as exc:  # noqa: BLE001 —— 取不到就当「全都受保护不了」是错的方向，故记录
        logger.warning("journal retention: running-run scan failed: %s", exc)
    try:
        from ...db import repositories as repo
        protected.update(await repo.list_paused_ai_trade_group_ids(session))
    except Exception as exc:  # noqa: BLE001
        logger.warning("journal retention: paused-contract scan failed: %s", exc)
    try:
        from .. import get_ai_trade_manager
        group = get_ai_trade_manager().group_status()
        if group and group.get("groupId"):
            protected.add(str(group["groupId"]))
    except Exception as exc:  # noqa: BLE001 —— 内存侧取不到不影响另外两处
        logger.warning("journal retention: in-memory group probe failed: %s", exc)
    return sorted(protected)


async def prune_with_session(session: AsyncSession, settings: Settings, *,
                             dry_run: bool) -> dict:
    """执行一次剪枝并回报读数。

    Args:
        session: 调用方开的 AsyncSession。
        settings: 生效设置（两把闸的读数来自它）。
        dry_run: 只数不删。

    Returns:
        `{"decisions", "events", "protectedGroups", "dryRun", "prunedTs"}`。
    """
    days = int(settings.ai_trade_journal_retention_days)
    max_rows = int(settings.ai_trade_journal_max_rows)
    protected = await collect_protected_groups(session)
    if days <= 0 and max_rows <= 0:
        # 两个都为 0 = 永不剪枝。**如实回一个零，而不是跳过端点**——用户点了按钮就该看到结果。
        return {"decisions": 0, "events": 0, "protectedGroups": protected,
                "dryRun": dry_run, "prunedTs": int(time.time())}
    before_ts = int(time.time()) - days * DAY_SECONDS if days > 0 else None
    stats = await journal_repo.prune_journal(session, before_ts=before_ts, max_rows=max_rows,
                                             protected=protected, dry_run=dry_run)
    return {**stats, "protectedGroups": protected, "dryRun": dry_run,
            "prunedTs": int(time.time())}


async def prune_journal_once(*, dry_run: bool = False) -> dict:
    """自开 session 的一次剪枝（启动后错峰任务用）。总闸关 ⇒ 直接回零，一次查询都不发。"""
    settings = get_settings()
    if not settings.ai_trade_journal_enabled:
        return {"decisions": 0, "events": 0, "protectedGroups": [], "dryRun": dry_run,
                "prunedTs": int(time.time())}
    from ...db.session import get_sessionmaker
    async with get_sessionmaker()() as session:
        return await prune_with_session(session, settings, dry_run=dry_run)
