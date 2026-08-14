"""分组层的暂停 / 继续（ai-trade-pause-resume §3.3 / G-11 · G-18 · G-28 · G-29 · G-30）。

本文件盯的是**互斥与容量闸的真值从哪来**。一句话：真值取**状态表**，不取内存——内存
`_paused_ref` 只是展示与互斥的缓存，而它的装载（lifespan 水合协程）按仓库惯例是「失败只
warning 不阻断启动」，一次偶发的水合失败绝不该变成「可以在一份带仓的暂停契约之上再开一个
新分组」（[P0-3]）。
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from app.ai_auto.manager import (AiTradeGroup, AiTradeGroupMember, AiTradePreflightError,
                                 AiTradeSessionLimitError, PausedGroupRef,
                                 get_ai_trade_manager, paused_ref_from_state_row)
from app.db import ai_trade_state_repositories as state_repo
from app.db.ai_trade_state_repositories import GroupStateWrite
from app.db.session import dispose_engine, get_sessionmaker, init_db

_APP_DIR = Path(__file__).resolve().parents[1] / "app"


class _FakeExecutor:
    """执行器桩：`group_status()` 的验收卡合并会读 `session.executor.settings`。"""

    def __init__(self):
        from app.config import Settings

        self.settings = Settings()
        self.budget_usd = 100.0
        self.leverage = 3.0


class _FakeSession:
    def __init__(self, inst_id: str, run_id: int, *, state: str = "running"):
        self.executor = _FakeExecutor()
        self.inst_id = inst_id
        self.run_id = run_id
        self.session_id = f"aitrade_{inst_id}"
        self.state = state
        self.stop_reason = None
        self.pause_reason = None
        self.paused_ts = 0
        self.group_id = ""
        self.on_stopped = None
        self.stopped_with_flatten: bool | None = None

    @property
    def is_active(self) -> bool:
        return self.state in ("running", "paused")

    async def pause(self, reason="user", *, flatten=False):
        self.state = "paused"
        self.pause_reason = reason
        return {"instId": self.inst_id, "sessionId": self.session_id, "runId": self.run_id,
                "state": "paused", "position": None, "positionUnknown": False,
                "backstop": None, "restingCancelled": None, "flattened": None}

    async def resume(self):
        self.state = "running"
        return {"ok": True, "state": self.state}

    # 平仓失败时真实 `stop()` 回的是 `{"ok": False, …, "residual": {...}}` 且**仍完成停止流程**
    # （它承诺「平仓失败也要完成停止」）。桩用这个开关复刻那条路径。
    flatten_fails = False

    async def stop(self, reason="user_stop", *, flatten=True):
        self.state = "stopped"
        self.stop_reason = reason
        self.stopped_with_flatten = flatten
        if self.on_stopped is not None:
            await self.on_stopped(self)
        if flatten and self.flatten_fails:
            return {"ok": False, "code": "LIVE_CLOSE_FAILED",
                    "residual": {"side": "long", "size": 0.6}}
        return {"ok": True, "closedSz": 0}

    def state_member_row(self):
        return {"instId": self.inst_id, "runId": self.run_id, "sessionId": self.session_id,
                "position": None, "positionUnknown": False, "restingClOrdId": None,
                "knownClOrdIds": []}

    def discipline_wire(self):
        return {"rows": [], "hint": None}

    def acceptance_bundle(self):
        return {"acceptance": None, "exits": None, "funding": None, "acceptanceInput": None}

    def info(self):
        return {"sessionId": self.session_id, "runId": self.run_id, "state": self.state,
                "instId": self.inst_id}


def _group(*insts: str, state: str = "running") -> AiTradeGroup:
    members = [AiTradeGroupMember(inst_id=inst, allocation_pct=100.0 / len(insts),
                                  budget_usd=100.0, session=_FakeSession(inst, idx + 1),
                                  run_id=idx + 1)
               for idx, inst in enumerate(insts)]
    return AiTradeGroup(group_id="g-mgr", bar="1H", strategy_ids=[1], strategy_names=["S"],
                        total_budget_usd=300.0, leverage=3.0, context_refresh_rounds=10,
                        created_ts=1_700_000_000, members=members, state=state,
                        account_id=7, start_wire={"accountId": 7})


def _wire_callbacks(manager, group: AiTradeGroup) -> None:
    """镜像 `_build_group` 的两条会话侧接线（W2 落点 + W5 回调）。"""
    from functools import partial

    for member in group.members:
        member.session.group_id = group.group_id
        member.session.on_stopped = partial(manager._note_session_stopped, group)


def _reset(manager) -> None:
    manager._group = None
    manager._paused_ref = None
    manager._hydration_failed = False


# ---------------- G-11（冻结）：`status()` 的消费者集合 ----------------
def test_status_consumers_are_frozen_to_the_mutual_exclusion_check():
    """G-11：`.status()` 的真实调用点只有 live_auto 的互斥判定与 API 层，且都只判 `is not None`。

    这是本轮**唯一**一处改变既有函数返回值语义的地方（冷占位下它不再返回 None）；一旦出现第二个
    读它字段的消费者，那处语义变更就会静默地弄坏别人。正则写 `\\r?\\n`（core.autocrlf=true）。
    """
    hits: list[tuple[str, str]] = []
    for path in _APP_DIR.rglob("*.py"):
        for line in re.split(r"\r?\n", path.read_text(encoding="utf-8")):
            if ".status()" in line and not line.strip().startswith("#"):
                hits.append((path.name, line.strip()))
    files = {name for name, _ in hits}
    assert files == {"manager.py"}, hits          # ai_auto/manager.py 的两行文档 + live_auto 的判定
    real_calls = [line for _, line in hits if not line.startswith("`")
                  and not line.startswith("**")]
    assert real_calls == ["if get_ai_trade_manager().status() is not None:"], real_calls


# ---------------- G-28：水合失败即盲区，绝不表示成「没有」 ----------------
async def test_hydration_failure_keeps_the_mutual_exclusion_alive(monkeypatch):
    """G-28 ①②③：水合失败 ⇒ `status()` 非空（互斥不失效）+ start 409；DB 恢复后盲区自愈。"""
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        manager.note_hydration_failed()
        assert manager.status() is not None       # ① 「我不知道」也算占位

        async def boom(*_a, **_k):
            raise RuntimeError("db busy")

        monkeypatch.setattr(state_repo, "load_paused_group_brief", boom)
        with pytest.raises(AiTradeSessionLimitError) as exc:
            await manager._require_capacity()
        assert exc.value.code == "AI_TRADE_RESUME_UNVERIFIABLE"   # ② fail-closed
        assert manager.status() is not None

        monkeypatch.undo()
        await manager._require_capacity()          # ③ DB 恢复且表中无 paused 行 ⇒ 放行
        assert manager._hydration_failed is False
        assert manager.status() is None            # 盲区被自愈
    finally:
        _reset(manager)
        await dispose_engine()


async def test_paused_contract_in_db_blocks_capacity_even_without_memory_state():
    """G-18 的服务层半：内存里什么都没有，但状态表有 paused 行 ⇒ 拒绝新分组。"""
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            await state_repo.upsert_group_state(s, GroupStateWrite(
                group_id="g-cold", status="paused", paused_ts=1_700_000_000))
        with pytest.raises(AiTradeSessionLimitError) as exc:
            await manager._require_capacity()
        assert exc.value.code == "AI_TRADE_PAUSED_GROUP_EXISTS"
    finally:
        _reset(manager)
        await dispose_engine()


# ---------------- G-9：`group` 与 `resumable` 永不同时非空 ----------------
async def test_group_and_resumable_are_mutually_exclusive():
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        ref = PausedGroupRef(group_id="g-cold", bar="15m", paused_ts=1_700_000_000,
                             members=[{"instId": "BTC-USDT-SWAP",
                                       "position": {"side": "long", "size": 0.6}}])
        manager.adopt_paused_ref(ref)
        assert manager.group_status() is None
        cold = manager.resumable()
        assert cold["groupId"] == "g-cold" and cold["hasPosition"] is True
        assert cold["offlineText"]
        assert manager.status() is not None        # 冷占位也要拦住 live-auto

        manager._group = _group("BTC-USDT-SWAP")   # 热态存在时冷占位一律不下发
        assert manager.resumable() is None
        assert manager.group_status()["state"] == "running"
    finally:
        _reset(manager)
        await dispose_engine()


def test_paused_ref_prefers_the_resolved_echo_over_the_raw_request():
    """展示字段优先取落地回执（预算会被夹紧，说错一个数比不说更坏）。"""
    ref = paused_ref_from_state_row({
        "groupId": "g", "bar": "15m", "accountId": 7,
        "startWire": {"capitalUsd": 900.0, "leverage": 5.0},
        "resolvedEcho": {"totalBudgetUsd": 300.0, "leverage": 3.0,
                         "strategyNames": ["趋势带波段"], "createdTs": 1_700_000_000},
        "members": [], "pausedTs": 1_700_000_100, "pauseReason": "user",
        "resumedCount": 2, "createdTs": 1_699_999_000})
    assert ref.total_budget_usd == 300.0 and ref.leverage == 3.0
    assert ref.strategy_names == ["趋势带波段"] and ref.resumed_count == 2


# ---------------- G-29：会话自终止后状态表必须收敛（W5）----------------
async def test_stopping_the_last_active_member_ends_the_contract():
    """G-29：停掉 paused 组的最后一个成员 ⇒ 状态表 `ended` + `self._group is None`。"""
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group = _group("BTC-USDT-SWAP", state="paused")
        for member in group.members:
            member.session.state = "paused"
        manager._group = group
        _wire_callbacks(manager, group)
        sm = get_sessionmaker()
        async with sm() as s:
            await state_repo.upsert_group_state(s, GroupStateWrite(
                group_id="g-mgr", status="paused",
                members=[m.session.state_member_row() for m in group.members]))

        result = await manager.stop_member("g-mgr", "BTC-USDT-SWAP")

        assert result is not None and manager._group is None
        async with sm() as s:
            row = await state_repo.load_group_state(s, "g-mgr")
        assert row["status"] == "ended"
    finally:
        _reset(manager)
        await dispose_engine()


async def test_stopping_a_non_last_member_keeps_the_group_and_drops_only_that_member():
    """[P1-10]：清组判据是 `is_active`——停掉非最后一个成员时占位必须留着，其余 paused 成员还在。"""
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group = _group("BTC-USDT-SWAP", "ETH-USDT-SWAP", state="paused")
        for member in group.members:
            member.session.state = "paused"
        manager._group = group
        _wire_callbacks(manager, group)
        sm = get_sessionmaker()
        async with sm() as s:
            await state_repo.upsert_group_state(s, GroupStateWrite(
                group_id="g-mgr", status="paused",
                members=[m.session.state_member_row() for m in group.members]))

        await manager.stop_member("g-mgr", "BTC-USDT-SWAP")

        assert manager._group is group             # 占位必须留着，否则续跑卡与互斥同时消失
        assert group.members[1].session.is_active
        async with sm() as s:
            row = await state_repo.load_group_state(s, "g-mgr")
        assert row["status"] == "paused"
        assert [m["instId"] for m in row["members"]] == ["ETH-USDT-SWAP"]
    finally:
        _reset(manager)
        await dispose_engine()


# ---------------- G-30：启动回滚绝不留幽灵契约 ----------------
async def test_launch_rollback_leaves_no_ghost_contract():
    """G-30（[P1-5]）：`_launch_group_or_rollback` 抛错 ⇒ 状态表零残留行，下次 start 不被挡住。"""
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group = _group("BTC-USDT-SWAP")

        async def boom():
            raise RuntimeError("start failed")

        group.members[0].session.start = boom
        sm = get_sessionmaker()
        async with sm() as s:                      # 模拟竞态下已写入的那一行
            await state_repo.upsert_group_state(s, GroupStateWrite(group_id="g-mgr",
                                                                   status="running"))
        manager._group = group
        with pytest.raises(RuntimeError):
            await manager._launch_group_or_rollback(group)

        assert manager._group is None
        async with sm() as s:
            assert await state_repo.load_group_state(s, "g-mgr") is None
            assert await state_repo.load_paused_group_brief(s) is None
        await manager._require_capacity()          # 不被 AI_TRADE_PAUSED_GROUP_EXISTS 挡住
    finally:
        _reset(manager)
        await dispose_engine()


# ---------------- 热暂停 / 热续跑 ----------------
async def test_hot_pause_keeps_the_group_and_hot_resume_restores_it():
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group = _group("BTC-USDT-SWAP", "ETH-USDT-SWAP")
        manager._group = group
        _wire_callbacks(manager, group)

        paused = await manager.pause_group("g-mgr", reason="user")
        assert paused["state"] == "paused" and len(paused["members"]) == 2
        assert manager._group is group             # **不清 `_group`**（热暂停的占位就是它自己）
        assert all(m.session.state == "paused" for m in group.members)

        resumed = await manager.resume_group("g-mgr", acknowledged=True)
        assert resumed.state == "running" and resumed.resumed_count == 1
        assert all(m.session.state == "running" for m in group.members)
    finally:
        _reset(manager)
        await dispose_engine()


async def test_resume_rejects_a_group_that_is_not_paused():
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        manager._group = _group("BTC-USDT-SWAP")
        with pytest.raises(Exception) as exc:
            await manager.resume_group("g-mgr", acknowledged=True)
        assert getattr(exc.value, "code", "") == "AI_TRADE_NOT_PAUSED"
    finally:
        _reset(manager)
        await dispose_engine()


async def test_pause_group_returns_none_for_an_unknown_group():
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        assert await manager.pause_group("nope") is None
    finally:
        _reset(manager)
        await dispose_engine()


async def test_stop_group_forgets_the_cold_placeholder():
    """I-2 的三处清空点之一：停整组后冷占位不得残留（否则会出一张指向空分组的续跑卡）。"""
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group = _group("BTC-USDT-SWAP")
        manager._group = group
        manager._paused_ref = PausedGroupRef(group_id="g-mgr")
        await manager.stop_group("g-mgr")
        assert manager._group is None and manager._paused_ref is None
        assert manager.status() is None
    finally:
        _reset(manager)
        await dispose_engine()


async def test_stop_group_flattens_but_end_group_can_hold_positions():
    """`end_group(flatten=False)` 走的是 `stop(flatten=False)`——本轮唯一一个非平仓的停止路径。"""
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group = _group("BTC-USDT-SWAP", state="paused")
        group.members[0].session.state = "paused"
        manager._group = group
        _wire_callbacks(manager, group)
        sm = get_sessionmaker()
        async with sm() as s:
            await state_repo.upsert_group_state(s, GroupStateWrite(
                group_id="g-mgr", status="paused",
                members=[{"instId": "BTC-USDT-SWAP", "runId": 1,
                          "position": {"side": "long", "size": 0.6}}]))

        result = await manager.end_group("g-mgr", flatten=False, acknowledged=True)

        assert result["flattened"] is False
        assert group.members[0].session.stopped_with_flatten is False
        assert result["orphanPositions"][0]["position"]["size"] == 0.6
    finally:
        _reset(manager)
        await dispose_engine()


async def test_end_reports_a_failed_flatten_as_an_orphan_position():
    """RV-1 的同族：热路径请求平仓但平不掉 ⇒ `flattened` 必须是 False，且那笔仓如实进
    `orphanPositions`。回执里这两个键**绝不能互相打架**——用户点的是「平掉并结束」，
    一句没兑现的承诺加一个空的孤儿仓列表，等于把仓藏起来。
    """
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group = _group("BTC-USDT-SWAP", state="paused")
        group.members[0].session.state = "paused"
        group.members[0].session.flatten_fails = True
        manager._group = group
        _wire_callbacks(manager, group)
        sm = get_sessionmaker()
        async with sm() as s:
            await state_repo.upsert_group_state(s, GroupStateWrite(
                group_id="g-mgr", status="paused",
                members=[{"instId": "BTC-USDT-SWAP", "runId": 1,
                          "position": {"side": "long", "size": 0.6}}]))

        result = await manager.end_group("g-mgr", flatten=True, acknowledged=False)

        assert result["flattened"] is False
        assert result["orphanPositions"][0]["instId"] == "BTC-USDT-SWAP"
        assert result["orphanPositions"][0]["position"] == {"side": "long", "size": 0.6}
    finally:
        _reset(manager)
        await dispose_engine()


async def test_cold_end_refuses_to_pretend_it_flattened():
    """RV-1：冷路径（进程里没有会话）下的 `flatten=True` 必须**当场拒绝**，绝不谎报已平仓。

    冷态只剩磁盘上的一份契约：没有 Executor、没有暖机窗口，也就没有任何东西能下那一单。照
    `flatten=True` 归档的话，回执会说「已平仓、无孤儿持仓」而交易所上那笔仓位原样还在——正是
    §1.2 第三段拿来论证本轮必要性的那个洞，本轮绝不能自己再造一个入口。
    """
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            await state_repo.upsert_group_state(s, GroupStateWrite(
                group_id="g-cold", status="paused",
                members=[{"instId": "BTC-USDT-SWAP", "runId": 1,
                          "position": {"side": "long", "size": 0.6}}]))

        with pytest.raises(AiTradePreflightError) as exc:
            await manager.end_group("g-cold", flatten=True, acknowledged=True)
        assert exc.value.code == "AI_TRADE_END_FLATTEN_UNAVAILABLE"

        async with sm() as s:                      # 拒绝必须是**无副作用**的：契约仍可续跑
            row = await state_repo.load_group_state(s, "g-cold")
        assert row["status"] == "paused"

        # 「保留持仓」那一支照旧可用，且回执如实带上仍留在交易所上的那笔仓。
        kept = await manager.end_group("g-cold", flatten=False, acknowledged=True)
        assert kept["flattened"] is False
        assert kept["orphanPositions"][0]["instId"] == "BTC-USDT-SWAP"
    finally:
        _reset(manager)
        await dispose_engine()


def test_unknown_position_is_treated_as_holding():
    """保守极性（与 S3 fail-closed 同源）：`positionUnknown` 一律**按带仓处理**。"""
    from app.ai_auto.manager import AiTradeManager

    assert AiTradeManager._has_recorded_position([{"positionUnknown": True}]) is True
    assert AiTradeManager._has_recorded_position([{"position": None}]) is False


async def test_note_session_stopped_survives_a_missing_contract_row():
    """W5 的例外极性：状态表行不在时不得抛——`stop()` 承诺「平仓失败也要完成停止流程」。"""
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group = _group("BTC-USDT-SWAP")
        manager._group = group
        _wire_callbacks(manager, group)
        await group.members[0].session.stop("session_stop_loss")
        assert group.members[0].session.state == "stopped"
    finally:
        _reset(manager)
        await dispose_engine()


def test_monitor_loop_predicate_uses_is_active():
    """交叉守卫：`AiTradeSession._monitor_loop` 的循环判据必须是 `is_active`（D-5 的根因两行）。"""
    import inspect

    from app.ai_auto.session import AiTradeSession

    source = inspect.getsource(AiTradeSession._monitor_loop)
    assert "while self.is_active:" in source and "if not self.is_active:" in source


def test_asyncio_is_imported_for_the_type_checkers():
    """占位断言：保持 `asyncio` 导入被使用（本文件的桩会话依赖它的事件循环语义）。"""
    assert asyncio.iscoroutinefunction(_FakeSession.pause)


# ---------------- shutdown 的分派与一键回滚 ----------------
async def test_shutdown_defaults_to_hold_and_never_flattens():
    """[P0-2]：默认 `ai_trade_shutdown_action="hold"` ⇒ 关软件 = 带仓暂停，**平仓路径零调用**。"""
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group = _group("BTC-USDT-SWAP")
        manager._group = group
        _wire_callbacks(manager, group)

        result = await manager.shutdown()

        assert result == {"action": "hold", "groupId": "g-mgr", "paused": True}
        assert group.state == "paused" and group.pause_reason == "app_quit"
        assert group.members[0].session.stopped_with_flatten is None   # stop() 一次都没被调用
    finally:
        _reset(manager)
        await dispose_engine()


async def test_shutdown_reverts_verbatim_when_the_pause_axis_is_off():
    """整轴总闸关 ⇒ 关停**逐字回退老行为**（平仓 + 落 Run 终态），不受 `shutdown_action` 默认值影响。"""
    from app.config import Settings

    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group = _group("BTC-USDT-SWAP")
        group.members[0].session.executor.settings = Settings(ai_trade_pause_enabled=False)
        manager._group = group
        _wire_callbacks(manager, group)

        result = await manager.shutdown()

        assert result["action"] == "flatten" and result["paused"] is False
        assert group.members[0].session.stopped_with_flatten is True
        assert manager._group is None
    finally:
        _reset(manager)
        await dispose_engine()


async def test_capacity_gate_is_bypassed_entirely_when_the_pause_axis_is_off(monkeypatch):
    """一键回滚必须回滚到**完整**的老行为：总闸关时连那条 fail-closed 的 DB 读都不该发生。"""
    from app.config import Settings

    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        async def boom(*_a, **_k):
            raise AssertionError("总闸关时不该读状态表")

        monkeypatch.setattr(state_repo, "load_paused_group_brief", boom)
        await manager._require_capacity(Settings(ai_trade_pause_enabled=False))
    finally:
        _reset(manager)
        await dispose_engine()
