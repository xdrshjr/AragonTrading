"""保留策略：双闸 + 最老优先 + **绝不删运行中分组的行**（ai-trade-audit-journal §2.3）。

「正在跑的会话的日志被删掉」是审计系统能犯的最严重的错，故 `test_running_group_rows_are_never_deleted`
是本文件的核心。两个闸都为 0 = 永不剪枝。
"""
from __future__ import annotations

import time

import pytest
from sqlalchemy import delete, func, select

from app.ai_auto.journal.retention import collect_protected_groups, prune_with_session
from app.config import Settings
from app.db.models import (
    AiTradeDecisionLog,
    AiTradeEventLog,
    AiTradePromptProfile,
    Strategy,
    StrategyRun,
)
from app.db.session import get_sessionmaker, init_db

from .test_ai_trade_journal_api import INST, NOW, decision_row

LIVE_GROUP = "grp-live"
DEAD_GROUP = "grp-dead"
DAY = 86400


async def _seed(*, old_rows: int = 3, fresh_rows: int = 2) -> None:
    await init_db()
    async with get_sessionmaker()() as session:
        for table in (AiTradeDecisionLog, AiTradeEventLog, AiTradePromptProfile):
            await session.execute(delete(table))
        # ⚠️ **绝不整表删 `strategy_runs`**：本仓的用例共用一个临时库，而 `equity_points` /
        # `trades` 的外键在 SQLite 上不被强制、Core 的 `delete()` 也不走 ORM 级联。整表删会把
        # 别的用例的净值点变成孤儿行，而 `INTEGER PRIMARY KEY` 在删掉最大 id 之后**会复用**它
        # ——于是后面某个用例新建的 run 会读到一段属于已删 run 的净值序列。
        # 症状离现场极远（`test_api.py::test_backtest_uses_requested_time_range` 的
        # `equity[-1].ts` 对不上），而单独跑那个用例又是绿的。只删自己造的那两组。
        await session.execute(
            delete(StrategyRun).where(StrategyRun.group_id.in_([LIVE_GROUP, DEAD_GROUP])))
        strategy = Strategy(template_key="ai_trade", name="ret", params_json="{}")
        session.add(strategy)
        await session.flush()
        session.add(StrategyRun(strategy_id=strategy.id, inst_id=INST, bar="15m",
                                mode="paper", status="running", start_ts=NOW,
                                group_id=LIVE_GROUP, config_json="{}"))
        session.add(StrategyRun(strategy_id=strategy.id, inst_id=INST, bar="15m",
                                mode="paper", status="done", start_ts=NOW - 10 * DAY,
                                group_id=DEAD_GROUP, config_json="{}"))
        for index in range(old_rows):
            row = decision_row(index + 1, group_id=DEAD_GROUP)
            row.created_ts = NOW - 200 * DAY
            session.add(row)
        for index in range(fresh_rows):
            row = decision_row(index + 100, group_id=LIVE_GROUP)
            row.created_ts = NOW - 300 * DAY      # 明明够老，但它属于运行中分组
            session.add(row)
        session.add(AiTradeEventLog(group_id=DEAD_GROUP, ts=NOW, wall_ts=NOW, level="info",
                                    category="lifecycle", code="GROUP_ENDED",
                                    message="已归档", created_ts=NOW - 200 * DAY))
        await session.commit()


async def _count() -> tuple[int, int]:
    async with get_sessionmaker()() as session:
        decisions = int((await session.execute(
            select(func.count(AiTradeDecisionLog.id)))).scalar() or 0)
        events = int((await session.execute(
            select(func.count(AiTradeEventLog.id)))).scalar() or 0)
    return decisions, events


@pytest.mark.asyncio
async def test_running_group_rows_are_never_deleted():
    """★ 判据取库侧 run 状态；即便这些行远比保留期更老，也一条都不许删。"""
    await _seed()
    settings = Settings(ai_trade_journal_retention_days=1, ai_trade_journal_max_rows=0)
    async with get_sessionmaker()() as session:
        protected = await collect_protected_groups(session)
        assert LIVE_GROUP in protected
        stats = await prune_with_session(session, settings, dry_run=False)
    assert LIVE_GROUP in stats["protectedGroups"]
    decisions, _events = await _count()
    async with get_sessionmaker()() as session:
        live_left = int((await session.execute(
            select(func.count(AiTradeDecisionLog.id))
            .where(AiTradeDecisionLog.group_id == LIVE_GROUP))).scalar() or 0)
    assert live_left == 2, "运行中分组的行一条不少"
    assert decisions == 2, "已归档分组的老行被删干净"


@pytest.mark.asyncio
async def test_dry_run_deletes_nothing():
    await _seed()
    settings = Settings(ai_trade_journal_retention_days=1, ai_trade_journal_max_rows=0)
    before = await _count()
    async with get_sessionmaker()() as session:
        stats = await prune_with_session(session, settings, dry_run=True)
    assert stats["decisions"] == 3 and stats["dryRun"] is True
    assert await _count() == before


@pytest.mark.asyncio
async def test_row_cap_deletes_oldest_first():
    await _seed(old_rows=5, fresh_rows=0)
    settings = Settings(ai_trade_journal_retention_days=0, ai_trade_journal_max_rows=2)
    async with get_sessionmaker()() as session:
        await prune_with_session(session, settings, dry_run=False)
        left = (await session.execute(
            select(AiTradeDecisionLog.round_no)
            .order_by(AiTradeDecisionLog.id.asc()))).scalars().all()
    assert sorted(left) == [4, 5], "按 id 升序删，留下最新的两条"


@pytest.mark.asyncio
async def test_both_gates_zero_means_never_prune():
    await _seed()
    settings = Settings(ai_trade_journal_retention_days=0, ai_trade_journal_max_rows=0)
    before = await _count()
    async with get_sessionmaker()() as session:
        stats = await prune_with_session(session, settings, dry_run=False)
    assert stats["decisions"] == 0 and stats["events"] == 0
    assert await _count() == before


@pytest.mark.asyncio
async def test_prune_reports_a_timestamp_even_when_nothing_was_deleted():
    """用户点了按钮就该看到结果——不许「什么都不做且什么都不说」。"""
    await _seed()
    settings = Settings(ai_trade_journal_retention_days=0, ai_trade_journal_max_rows=0)
    async with get_sessionmaker()() as session:
        stats = await prune_with_session(session, settings, dry_run=True)
    assert stats["prunedTs"] <= int(time.time()) and stats["prunedTs"] > 0
