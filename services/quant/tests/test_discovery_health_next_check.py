"""``next_check_ts`` 的四条分支（discovery-asset-ledger §2.3 末 · 评审 P1-5）。

正常路径不推进，就等于 ``list_due_assets`` 的 ``ORDER BY next_check_ts ASC LIMIT batch``
让队头那 ``batch`` 条资产**每 tick 被反复体检**、其余永远排不上——而 ``summary.guard`` 里
``checkedTotal`` 一路上涨、``skippedByCap`` 为 0、``lastError`` 为空，**一切正常**。
末尾那条端到端用例（连续三 tick 必须取到不同资产）就是这条空转的机器判据。
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete

from app.config import Settings
from app.db.models import DiscoveryAsset
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.discovery import asset_repositories as asset_repo
from app.discovery import health

STEP = 900
TS0 = 1_700_000_000


def _limits() -> health.HealthLimits:
    return health.HealthLimits.of(Settings())


@pytest.mark.parametrize("status", ["healthy", "watch", "decayed"])
def test_settled_status_advances_by_one_interval(status: str) -> None:
    limits = _limits()
    plan = health.SchedulePlan(status=status, now=TS0, step=STEP, fresh_bars=500)
    assert health.next_check_ts(plan, limits) == TS0 + int(limits.interval_s)


def test_unknown_waits_until_the_bars_can_accumulate() -> None:
    limits = _limits()
    plan = health.SchedulePlan(status="unknown", now=TS0, step=STEP, fresh_bars=0)
    assert health.next_check_ts(plan, limits) == TS0 + limits.min_fresh_bars * STEP


@pytest.mark.parametrize("errors, multiplier", [(0, 1), (1, 2), (2, 4), (3, 8), (9, 8)])
def test_error_backs_off_exponentially_with_a_ceiling(errors: int, multiplier: int) -> None:
    limits = _limits()
    plan = health.SchedulePlan(status="error", now=TS0, step=STEP, fresh_bars=500,
                               consecutive_error=errors)
    assert health.next_check_ts(plan, limits) == TS0 + int(limits.interval_s) * multiplier


# ============================ 端到端：队列必须真的前进 ============================
async def _seed(session, count: int, due_ts: int) -> list[int]:
    ids = []
    for index in range(count):
        row = await asset_repo.create_asset(session, asset_repo.AssetValues(
            asset_key=f"f:queue{index:032d}"[:48], kind="factor", inst_id="BTC-USDT-SWAP",
            bar="15m", horizon=6, direction="long", expr_text="ts_mean(ret,20)",
            genome={}, describe_text="", mechanism="", label=f"queue-{index}",
            found_ts=TS0, source={}, mined_window={"endTs": TS0}, accept_metrics={},
            accept_thresholds={}, trials_at_accept=0, next_check_ts=due_ts + index))
        ids.append(int(row.id))
    return ids


async def test_three_consecutive_ticks_pick_different_assets() -> None:
    """三个 tick 依次取到**不同**的资产（不推进 ``next_check_ts`` 的实现会三次取到同一批）。"""
    await init_db()
    sm = get_sessionmaker()
    try:
        async with sm() as session:
            await session.execute(delete(DiscoveryAsset))
            await session.commit()
            await _seed(session, 6, TS0 - 100)
            limits = _limits()
            picked = []
            for tick in range(3):
                due = await asset_repo.list_due_assets(session, now=TS0, limit=2)
                picked.append({int(row.id) for row in due})
                for row in due:
                    plan = health.SchedulePlan(status="healthy", now=TS0, step=STEP,
                                               fresh_bars=500)
                    row.next_check_ts = health.next_check_ts(plan, limits)
                await session.commit()
        assert all(len(batch) == 2 for batch in picked)
        assert picked[0] & picked[1] == set()
        assert picked[1] & picked[2] == set()
        assert len(picked[0] | picked[1] | picked[2]) == 6
    finally:
        await dispose_engine()
