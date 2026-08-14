"""可续跑契约的持久化（ai-trade-pause-resume §3.4 / §3.7 / G-16 ~ G-20）。

覆盖：状态表 CRUD、启动对账 `running → paused`、二次启动幂等、老库自动建表（零 `ALTER`）、
超龄判定、历史列表按 DB `status` 排除 paused。

为什么是新表而不是给 `strategy_runs` 加列：`create_all` **不给既有表补列**（本仓库有四个
`_ensure_*_columns` 补丁作为成文教训），而它**会**建缺失的表 ⇒ 新表 = 零迁移、老库自动获得。
"""
from __future__ import annotations

import time

from sqlalchemy import text

from app.config import Settings
from app.db import ai_trade_state_repositories as state_repo
from app.db import repositories as repo
from app.db.ai_trade_state_repositories import GroupStateWrite
from app.db.models import StrategyRun
from app.db.session import dispose_engine, get_engine, get_sessionmaker, init_db

_START_TS = 1_700_000_000


def _write(group_id: str, status: str = "running", **over) -> GroupStateWrite:
    kwargs = {"group_id": group_id, "status": status, "account_id": 7, "bar": "15m",
              "start_wire": {"accountId": 7, "bar": "15m"},
              "resolved_echo": {"totalBudgetUsd": 300.0, "leverage": 3.0},
              "members": [{"instId": "BTC-USDT-SWAP", "runId": 1,
                           "position": {"side": "long", "size": 0.6, "posId": "POS-A"},
                           "positionUnknown": False, "knownClOrdIds": ["aragonA"]}]}
    kwargs.update(over)
    return GroupStateWrite(**kwargs)


async def _add_run(session, *, group_id: str, status: str = "running") -> int:
    run = await repo.create_run(session, strategy_id=1, inst_id="BTC-USDT-SWAP", bar="15m",
                                mode="ai-trade", config={"mode": "ai-trade"}, status=status,
                                group_id=group_id, group_started_ts=_START_TS,
                                group_total_budget_usd=300.0)
    return run.id


async def test_state_row_round_trips_and_upsert_is_idempotent():
    await init_db()
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            await state_repo.upsert_group_state(s, _write("g-crud"))
            row = await state_repo.load_group_state(s, "g-crud")
        assert row["status"] == "running" and row["accountId"] == 7
        assert row["startWire"]["bar"] == "15m"
        assert row["members"][0]["position"]["posId"] == "POS-A"

        async with sm() as s:                       # 同 group_id 再写一次 = 更新，不是插两行
            await state_repo.upsert_group_state(s, _write("g-crud", status="paused",
                                                          pause_reason="user",
                                                          paused_ts=_START_TS))
            again = await state_repo.load_group_state(s, "g-crud")
            brief = await state_repo.load_paused_group_brief(s)
        assert again["status"] == "paused" and again["pauseReason"] == "user"
        assert brief is not None and brief["groupId"] == "g-crud"
    finally:
        await dispose_engine()


async def test_patch_member_state_only_touches_that_member():
    await init_db()
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            await state_repo.upsert_group_state(s, _write(
                "g-member", members=[{"instId": "BTC-USDT-SWAP", "runId": 1},
                                     {"instId": "ETH-USDT-SWAP", "runId": 2}]))
            hit = await state_repo.patch_member_state(
                s, "g-member", "ETH-USDT-SWAP", {"position": {"side": "short", "size": 2.0}})
            miss = await state_repo.patch_member_state(s, "g-member", "SOL-USDT-SWAP", {})
            row = await state_repo.load_group_state(s, "g-member")
        assert hit is True and miss is False
        assert row["members"][0].get("position") is None
        assert row["members"][1]["position"]["side"] == "short"
    finally:
        await dispose_engine()


async def test_patch_and_delete_on_missing_row_never_creates_one():
    """绝不静默新建一行没人认领的契约（缺行即 False / 0）。"""
    await init_db()
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            assert await state_repo.patch_group_state(s, "ghost", {"status": "ended"}) is False
            assert await state_repo.delete_group_state(s, "ghost") == 0
            assert await state_repo.load_group_state(s, "ghost") is None
    finally:
        await dispose_engine()


async def test_old_database_gets_the_table_from_create_all():
    """G-17（零 `ALTER` 验证）：把表 DROP 掉再跑一次 `init_db` ⇒ 表被建出、读写正常。"""
    await init_db()
    try:
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS ai_trade_group_states"))
            rows = (await conn.execute(text(
                "SELECT name FROM sqlite_master WHERE name='ai_trade_group_states'"))).all()
        assert rows == []

        await init_db()                              # 老库路径：create_all 建缺失的表
        sm = get_sessionmaker()
        async with sm() as s:
            await state_repo.upsert_group_state(s, _write("g-oldlib"))
            assert (await state_repo.load_group_state(s, "g-oldlib"))["status"] == "running"
    finally:
        await dispose_engine()


async def test_reconcile_keeps_legacy_behaviour_when_resume_is_disabled():
    """G-16：`resume_enabled=False` ⇒ **逐字回退老行为**（running → stopped + end_ts + reconciled）。"""
    await init_db()
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            run_id = await _add_run(s, group_id="g-legacy")
            first = await repo.reconcile_orphan_ai_trade_runs(s)
            second = await repo.reconcile_orphan_ai_trade_runs(s)
            run = await s.get(StrategyRun, run_id)
        # `>= 1` 而不是 `== 1`：本仓库的用例**共用一个临时 DB**，别的文件可能留下别的 running
        # ai-trade run。要钉的是「返回的是 int 条数」与「二次运行幂等」，不是那个具体数字。
        assert isinstance(first, int) and first >= 1 and second == 0
        assert run.status == "stopped" and run.end_ts is not None
        assert '"reconciled": true' in run.metrics_json
    finally:
        await dispose_engine()


async def test_reconcile_parks_runs_as_paused_when_resume_is_enabled():
    """G-16：`resume_enabled=True` ⇒ `running → paused`，且 **end_ts 仍为 NULL**；二次启动幂等。"""
    await init_db()
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            run_id = await _add_run(s, group_id="g-park")
            first = await repo.reconcile_orphan_ai_trade_runs(s, resume_enabled=True)
            second = await repo.reconcile_orphan_ai_trade_runs(s, resume_enabled=True)
            run = await s.get(StrategyRun, run_id)
            paused_groups = await repo.list_paused_ai_trade_group_ids(s)
        assert isinstance(first, int) and first >= 1 and second == 0   # 见上：共用临时库
        assert run.status == "paused" and run.end_ts is None
        assert '"reconciled"' not in (run.metrics_json or "")
        assert "g-park" in paused_groups
    finally:
        await dispose_engine()


async def test_disabled_ai_master_switch_still_parks_instead_of_archiving(monkeypatch):
    """[P1-11]：`pause_enabled=True & ai_trade_enabled=False` ⇒ 仍置 `paused`，`pause_reason`
    为 `disabled_by_config`（**不归档**）。「运维关掉 AI 总闸」绝不该等于「把一个仍持真实仓位
    的会话静默归档」。"""
    await init_db()
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            run_id = await _add_run(s, group_id="g-cfg")
            await state_repo.upsert_group_state(s, _write("g-cfg"))
        monkeypatch.setattr("app.db.session.get_settings",
                            lambda: Settings(ai_trade_enabled=False,
                                             ai_trade_pause_enabled=True))
        from app.db.session import _reconcile_orphan_ai_trade_runs

        await _reconcile_orphan_ai_trade_runs()
        async with sm() as s:
            run = await s.get(StrategyRun, run_id)
            row = await state_repo.load_group_state(s, "g-cfg")
        assert run.status == "paused"
        assert row["status"] == "paused" and row["pauseReason"] == "disabled_by_config"
    finally:
        await dispose_engine()


async def test_pause_axis_master_switch_off_falls_back_to_the_old_behaviour(monkeypatch):
    """整轴总闸关 ⇒ 一键回滚：run 归档、状态表**不参与**（`pause_reason` 不被写）。"""
    await init_db()
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            run_id = await _add_run(s, group_id="g-off")
            await state_repo.upsert_group_state(s, _write("g-off"))
        monkeypatch.setattr("app.db.session.get_settings",
                            lambda: Settings(ai_trade_pause_enabled=False))
        from app.db.session import _reconcile_orphan_ai_trade_runs

        await _reconcile_orphan_ai_trade_runs()
        async with sm() as s:
            run = await s.get(StrategyRun, run_id)
            row = await state_repo.load_group_state(s, "g-off")
        assert run.status == "stopped"
        assert row["status"] == "running" and row["pauseReason"] is None
    finally:
        await dispose_engine()


async def test_history_list_excludes_paused_groups_but_keeps_running_semantics():
    """G-19 / D-8：历史列表按 **DB status** 排除 paused；`running` 的排除仍由既有
    `exclude_group_id` 承担（加它会打破 `test_history_endpoints`，[P0-3] 的落地口径）。"""
    await init_db()
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            await _add_run(s, group_id="g-hist-run", status="running")
            await _add_run(s, group_id="g-hist-paused", status="paused")
            await _add_run(s, group_id="g-hist-done", status="stopped")
            everything = await repo.list_ai_trade_groups(s, limit=50)
            filtered = await repo.list_ai_trade_groups(s, limit=50,
                                                       exclude_statuses=("paused",))
        assert {i["groupId"] for i in everything["items"]} >= {
            "g-hist-run", "g-hist-paused", "g-hist-done"}
        ids = {i["groupId"] for i in filtered["items"]}
        assert "g-hist-paused" not in ids
        assert {"g-hist-run", "g-hist-done"} <= ids   # 默认不排 running（既有口径不动）

        async with sm() as s:                          # G-19 的另一半：详情**仍可查**
            detail = await repo.get_ai_trade_group_detail(s, "g-hist-paused")
        assert detail is not None and detail["members"][0]["status"] == "paused"
    finally:
        await dispose_engine()


def test_age_gate_only_blocks_resume_not_archive():
    """G-20 的纯核心半：超龄只影响「能不能续」，`end` 那条路永远开着。"""
    from app.ai_auto import pause as pause_core

    now = int(time.time())
    assert pause_core.is_age_exceeded(now - 8 * 86400, now, 604800.0) is True
    assert pause_core.is_age_exceeded(now - 6 * 86400, now, 604800.0) is False
