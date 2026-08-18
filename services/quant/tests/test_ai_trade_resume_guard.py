"""续跑守卫（ai-trade-lifecycle-truth B-1 / B-2 / B-6 / B-7 · §8.1 用例 4~6 · 10 · 20 · 24 · 28）。

本文件守的是本轮的核心不变式 **L-5：续跑要么全组武装成功，要么整组拒绝** —— 不存在「置了
running 但有成员没起来」的中间态。用户报告的那句「貌似已经恢复了，但是 Agent 却没有正常自动
交易」，在代码里就是 `for member in group.members: await member.session.resume()` 这一行**没有
接住回执**，加上 `group.state = RUNNING` 无条件执行。

以及 **L-1：分组生命周期是派生量** —— `group_status()["state"]` 必须由各成员会话态现算，
`AiTradeGroup.state` 降级为 `declaredState`（意图）。
"""
from __future__ import annotations

import pytest

from app.ai_auto.manager import (AiTradeGroup, AiTradeGroupEnded, AiTradeGroupMember,
                                 AiTradePreflightError, AiTradeResumeNotArmed,
                                 AiTradeResumePartiallyDead, get_ai_trade_manager)
from app.ai_auto import lifecycle as lifecycle_core
from app.ai_auto import pause as pause_core
from app.db import ai_trade_state_repositories as state_repo
from app.db.ai_trade_state_repositories import GroupStateWrite
from app.db.session import dispose_engine, get_sessionmaker, init_db


class _FakeExecutor:
    def __init__(self):
        from app.config import Settings

        self.settings = Settings()
        self.budget_usd = 100.0
        self.leverage = 3.0
        self.bar_anchor_ts = 0


class _FakeSession:
    """带 `lifecycle_readings()` 的会话替身：读数由用例直接摆布，不碰任何真实时钟。"""

    def __init__(self, inst_id: str, run_id: int, *, state: str = "paused"):
        self.executor = _FakeExecutor()
        self.inst_id = inst_id
        self.run_id = run_id
        self.session_id = f"aitrade_{inst_id}"
        self.state = state
        self.stop_reason = None
        self.pause_reason = None
        self.paused_ts = 0
        self.resumed_ts = 0
        self.rounds_completed = 0
        self.group_id = ""
        self.on_stopped = None
        self.resume_ok = True             # False ⇒ 复刻「resume() 回 ok=False」那条路径
        self.link_state = "up"
        self.feed_age_s = 1.0
        self.stale_after_s = 10_800.0
        self.warmed_bars = 300
        self.flatten_calls: list[bool] = []
        self.pause_reasons: list[str] = []

    @property
    def is_active(self) -> bool:
        return self.state in ("running", "paused")

    def lifecycle_readings(self) -> lifecycle_core.MemberReadings:
        return lifecycle_core.MemberReadings(
            inst_id=self.inst_id, state=self.state, stop_reason=self.stop_reason,
            pause_reason=self.pause_reason, warmed_bars=self.warmed_bars,
            feed_age_s=self.feed_age_s, stale_after_s=self.stale_after_s,
            link_state=self.link_state, next_wake_in_s=120.0, next_bar_in_s=480.0,
            rounds_completed=self.rounds_completed)

    def lifecycle_wire(self) -> dict:
        return lifecycle_core.member_lifecycle_wire(
            self.lifecycle_readings(), paused_ts=self.paused_ts, resumed_ts=self.resumed_ts)

    async def resume(self) -> dict:
        if self.state != "paused" or not self.resume_ok:
            reason, text = lifecycle_core.classify_member(self.lifecycle_readings())
            return {"ok": False, "state": self.state, "reason": reason, "reasonText": text}
        self.state = "running"
        self.pause_reason = None
        return {"ok": True, "state": self.state, "lifecycle": self.lifecycle_wire()}

    async def pause(self, reason="user", *, flatten=False):
        self.pause_reasons.append(reason)
        self.flatten_calls.append(flatten)
        self.state = "paused"
        self.pause_reason = reason
        return {"instId": self.inst_id, "sessionId": self.session_id, "runId": self.run_id,
                "state": "paused", "position": None, "positionUnknown": False,
                "backstop": None, "restingCancelled": None, "flattened": None}

    async def stop(self, reason="user_stop", *, flatten=True):
        self.state = "stopped"
        self.stop_reason = reason
        if self.on_stopped is not None:
            await self.on_stopped(self)
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


def _group(*insts: str, group_id: str = "g-resume",
           state: str = pause_core.STATUS_PAUSED) -> AiTradeGroup:
    members = [AiTradeGroupMember(inst_id=inst, allocation_pct=100.0 / len(insts),
                                  budget_usd=100.0,
                                  session=_FakeSession(inst, idx + 1), run_id=idx + 1)
               for idx, inst in enumerate(insts)]
    return AiTradeGroup(group_id=group_id, bar="1H", strategy_ids=[1], strategy_names=["S"],
                        total_budget_usd=300.0, leverage=3.0, context_refresh_rounds=10,
                        created_ts=1_700_000_000, members=members, state=state,
                        paused_ts=1_700_000_100, pause_reason="user",
                        account_id=7, start_wire={"accountId": 7})


def _wire_callbacks(manager, group: AiTradeGroup) -> None:
    from functools import partial

    for member in group.members:
        member.session.group_id = group.group_id
        member.session.on_stopped = partial(manager._note_session_stopped, group)


def _reset(manager) -> None:
    manager._group = None
    manager._paused_ref = None
    manager._hydration_failed = False
    manager._last_logged_mismatch = None


async def _seed_contract(group: AiTradeGroup, status: str = "paused") -> None:
    async with get_sessionmaker()() as s:
        await state_repo.upsert_group_state(s, GroupStateWrite(
            group_id=group.group_id, status=status, paused_ts=group.paused_ts or None,
            members=[m.session.state_member_row() for m in group.members]))


# ---------------- 用例 4（D-1）：成员死了就整组拒绝，绝不置 running 后回 200 ----------------
async def test_resume_is_rejected_when_a_member_session_died():
    """D-1 的直接回归守卫：2 成员分组，其中一个在暂停期自终止 ⇒ **409**，且分组仍是 `paused`。

    这是用户报告现象的最直接一条路径：今天 `resume_group` 丢弃逐成员回执，已死的那个安静地留在
    `stopped`，分组照样被置 `running` 并回 200 —— 「恢复成功、Agent 不交易」。
    """
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group = _group("BTC-USDT-SWAP", "ETH-USDT-SWAP")
        group.members[1].session.state = "stopped"
        group.members[1].session.stop_reason = "session_stop_loss"
        manager._group = group
        await _seed_contract(group)

        with pytest.raises(AiTradeResumePartiallyDead) as exc:
            await manager.resume_group("g-resume", acknowledged=True)

        assert exc.value.code == "AI_TRADE_RESUME_PARTIALLY_DEAD"
        assert exc.value.status == 409
        assert group.state == pause_core.STATUS_PAUSED       # 绝不置 running
        assert group.members[0].session.state == "paused"    # 活着那个也没被叫醒
        rows = {row["instId"]: row for row in exc.value.members}
        assert "触发会话止损" in rows["ETH-USDT-SWAP"]["message"]
        assert rows["BTC-USDT-SWAP"]["code"] == "paused"
    finally:
        _reset(manager)
        await dispose_engine()


async def test_resume_of_a_fully_dead_group_heals_itself_and_refuses():
    """§3.6 第一格：全成员已终止 ⇒ 409 `AI_TRADE_GROUP_ENDED` + **自愈清组**。"""
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group = _group("BTC-USDT-SWAP")
        group.members[0].session.state = "stopped"
        group.members[0].session.stop_reason = "session_stop_loss"
        manager._group = group
        await _seed_contract(group)

        with pytest.raises(AiTradeGroupEnded) as exc:
            await manager.resume_group("g-resume", acknowledged=True)

        assert exc.value.status == 409
        assert "会话止损" in exc.value.message
        assert manager._group is None            # 僵尸分组被自愈掉了
        # **契约也要一并收敛**：只清内存不收敛磁盘的话，用户照着这句「请重新启动一个新的分组」
        # 去点，会被容量闸拿 `AI_TRADE_PAUSED_GROUP_EXISTS` 挡回来 —— 一句给不出出路的错误
        # 提示，比不给提示更坏。
        async with get_sessionmaker()() as s:
            row = await state_repo.load_group_state(s, "g-resume")
        assert row["status"] == "ended"
        await manager._require_no_paused_group()   # 不抛 ⇒ 用户真的可以再开一个新分组
    finally:
        _reset(manager)
        await dispose_engine()


# ---------------- 用例 5（L-5）：复检不过就整组回滚 ----------------
async def test_resume_rolls_back_when_post_check_fails():
    """第二个成员 `resume()` 回 `ok=False` ⇒ 第一个被回滚到 `paused` 且抛 `AI_TRADE_RESUME_NOT_ARMED`。"""
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group = _group("BTC-USDT-SWAP", "ETH-USDT-SWAP")
        group.members[1].session.resume_ok = False
        manager._group = group
        await _seed_contract(group)

        with pytest.raises(AiTradeResumeNotArmed) as exc:
            await manager.resume_group("g-resume", acknowledged=True)

        assert exc.value.code == "AI_TRADE_RESUME_NOT_ARMED"
        assert group.state == pause_core.STATUS_PAUSED
        first = group.members[0].session
        assert first.state == "paused"
        # 用例 28（[评审 R1-4]）：回滚后的暂停原因必须是**稳定枚举里真的有的那一个**，
        # 且在 `PAUSE_REASON_TEXT` 里有中文 —— 否则界面上是空的。
        assert first.pause_reason == pause_core.PAUSE_REASON_RESUME_ROLLBACK
        assert pause_core.PAUSE_REASON_TEXT[pause_core.PAUSE_REASON_RESUME_ROLLBACK]
        assert first.flatten_calls == [False]     # 回滚**绝不平仓**
        assert group.pause_reason == pause_core.PAUSE_REASON_RESUME_ROLLBACK
    finally:
        _reset(manager)
        await dispose_engine()


# ---------------- 回执必须真的被读到（Review 修）：ok=False 而读数看起来正常 ----------------
async def test_a_declining_receipt_alone_is_enough_to_refuse():
    """会话回了 `ok=False` 却把自己置成了 `running` ⇒ 读数判「已武装」，只有回执还说着真话。

    这一格守的是 B-2 ② 那句「**收集**回执，绝不丢弃」。曾经的写法按 `instId` 给回执建索引，
    而 `session.resume()` 的回执形状是 `{"ok", "state", "reason"?, "lifecycle"?}` ——
    **里面没有 instId**：那张表永远查不中，于是 D-1 那个被丢掉的赋值换了个地方原样复发，
    而且守卫自己看起来还是绿的。回执现在按**位置**对齐（② 那行推导式逐成员顺序生成）。
    """
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group = _group("BTC-USDT-SWAP", "ETH-USDT-SWAP")
        second = group.members[1].session

        async def _declines_but_looks_fine() -> dict:
            second.state = "running"      # 读数因此判 armed —— 只有回执知道它没起来
            return {"ok": False, "state": "running",
                    "reason": lifecycle_core.NOT_ARMED_STOPPED}

        second.resume = _declines_but_looks_fine
        manager._group = group
        await _seed_contract(group)

        with pytest.raises(AiTradeResumeNotArmed) as exc:
            await manager.resume_group("g-resume", acknowledged=True)

        assert [row["instId"] for row in exc.value.members] == ["ETH-USDT-SWAP"]
        assert "ETH-USDT-SWAP" in exc.value.message      # 拒绝必须说清是**谁**没起来
        assert group.state == pause_core.STATUS_PAUSED
        assert group.members[0].session.state == "paused"    # 已起来的那个被整组回滚
    finally:
        _reset(manager)
        await dispose_engine()


# ---------------- 用例 24（[评审 R0-5]）：拒绝续跑，而不是几秒后强平 ----------------
async def test_resume_refuses_instead_of_flattening_a_stale_group():
    """`live_auto_flatten_on_stale=True` 且行情陈旧超过 `stale_after × 4` 时点「继续」。

    今天 `resume()` 把时钟重置为 `now`，恰好挡住了「点继续 → 几秒后被强平」这条路；B-4 让时钟
    变得如实之后，这道保护必须由**复检**接管：判到 `feed_stale` ⇒ 不武装 ⇒ 回滚回 `paused`，
    **没有任何平仓调用发生**。
    """
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group = _group("BTC-USDT-SWAP")
        session = group.members[0].session
        session.executor.settings.live_auto_flatten_on_stale = True
        session.feed_age_s = 50_000.0             # 远超 stale_after × 4
        manager._group = group
        await _seed_contract(group)

        with pytest.raises(AiTradeResumeNotArmed) as exc:
            await manager.resume_group("g-resume", acknowledged=True)

        assert exc.value.members[0]["code"] == lifecycle_core.NOT_ARMED_STALE
        assert "退回暂停态" in exc.value.message
        assert "没有新 K 线" in exc.value.message
        assert group.state == pause_core.STATUS_PAUSED
        assert session.flatten_calls == [False], "绝不允许「继续」变成一次强平"
    finally:
        _reset(manager)
        await dispose_engine()


async def test_resume_refuses_when_the_link_is_down():
    """§3.6：链路不可达同样不武装（文案要说得出「实时链路不可达」）。"""
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group = _group("BTC-USDT-SWAP")
        group.members[0].session.link_state = "down"
        manager._group = group
        await _seed_contract(group)

        with pytest.raises(AiTradeResumeNotArmed) as exc:
            await manager.resume_group("g-resume", acknowledged=True)

        assert "实时链路不可达" in exc.value.message
    finally:
        _reset(manager)
        await dispose_engine()


async def test_a_healthy_group_resumes_and_reports_a_derived_running_state():
    """正常路径（本轮不许弄坏它）：全 paused ⇒ 全部武装 ⇒ 置 running + 派生态也是 running。"""
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group = _group("BTC-USDT-SWAP", "ETH-USDT-SWAP")
        manager._group = group
        await _seed_contract(group)

        resumed = await manager.resume_group("g-resume", acknowledged=True)

        assert resumed is group and group.state == pause_core.STATUS_RUNNING
        assert group.resumed_count == 1 and group.paused_ts == 0
        wire = manager.group_status()
        assert wire["state"] == "running" and wire["declaredState"] == "running"
        assert wire["lifecycle"]["armed"] is True
        assert wire["lifecycle"]["membersArmed"] == 2
        assert wire["lifecycle"]["membersTotal"] == 2
        assert wire["lifecycle"]["notArmed"] == []
        assert wire["members"][0]["lifecycle"]["armed"] is True
    finally:
        _reset(manager)
        await dispose_engine()


async def test_resuming_a_running_group_is_still_rejected():
    """既有语义不变：不处于暂停态 ⇒ 409 `AI_TRADE_NOT_PAUSED`。"""
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group = _group("BTC-USDT-SWAP", state=pause_core.STATUS_RUNNING)
        group.members[0].session.state = "running"
        manager._group = group

        with pytest.raises(AiTradePreflightError) as exc:
            await manager.resume_group("g-resume", acknowledged=True)
        assert exc.value.code == "AI_TRADE_NOT_PAUSED"
    finally:
        _reset(manager)
        await dispose_engine()


# ---------------- 用例 6 / 20（D-2 + [评审 R0-1]）：自终止后清组，但**只清自己的** ----------------
async def test_group_is_cleared_when_the_last_session_self_terminates():
    """用例 6（D-2）：最后一个成员自终止 ⇒ `status()` 与 `group_status()` 双双回 None。

    今天 `_note_session_stopped` 在「无任何活跃成员」时只置 `ended` 并清冷占位，**从不清
    `self._group`** ⇒ `/ai-trade/status` 永久回一个 `state:"running"` 而成员全 `stopped` 的
    僵尸分组，`live_auto` 的互斥闸被它永久占住。
    """
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group = _group("BTC-USDT-SWAP")
        manager._group = group
        _wire_callbacks(manager, group)
        await _seed_contract(group)

        await group.members[0].session.stop("session_stop_loss")

        assert manager._group is None
        assert manager.group_status() is None
        assert manager.status() is None
        async with get_sessionmaker()() as s:
            row = await state_repo.load_group_state(s, "g-resume")
        assert row["status"] == "ended"
    finally:
        _reset(manager)
        await dispose_engine()


async def test_stop_of_old_group_does_not_clear_new_group():
    """用例 20（[评审 R0-1]）：**身份守卫不可省**。

    组 A 的最后一个会话延迟自终止时，`self._group` 可能已经是组 B；无条件 `self._group = None`
    会把新分组连同它占着的互斥位一起抹掉 —— 界面整块消失而 Agent 其实还在跑，正是本轮要消灭的
    那句谎话的**镜像版**。
    """
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group_a = _group("BTC-USDT-SWAP", group_id="g-old")
        manager._group = group_a
        _wire_callbacks(manager, group_a)
        await _seed_contract(group_a)
        await group_a.members[0].session.stop("session_stop_loss")
        assert manager._group is None

        group_b = _group("ETH-USDT-SWAP", group_id="g-new",
                         state=pause_core.STATUS_RUNNING)
        group_b.members[0].session.state = "running"
        manager._group = group_b
        await _seed_contract(group_b, status="running")

        # 组 A 的**旧回调**再响一次（延迟自终止 / 重复回调）。
        await manager._note_session_stopped(group_a, group_a.members[0].session)

        assert manager._group is group_b, "旧分组的回调绝不能清掉新分组"
        assert manager.group_status()["groupId"] == "g-new"
    finally:
        _reset(manager)
        await dispose_engine()


# ---------------- 用例 10（D-11）：空回执不许抹掉真实持仓快照 ----------------
def test_pause_receipt_without_position_does_not_wipe_snapshot():
    """B-7：判据是「回执里**真的有** `position` 这个键」，不是「有回执」。

    成员非 `running` 时 `pause()` 的早退回执没有 `position` 键；照 `receipt is not None` 覆盖
    会把契约里「这个标的还有仓」这个事实抹成 `None` —— 续跑复核与「结束」的孤儿仓提示同时失真。
    """
    from app.ai_auto.manager import AiTradeManager

    group = _group("BTC-USDT-SWAP")
    truth = {"side": "long", "size": 0.6, "posId": "p1", "openCTime": 1_700_000_000}

    class _Holding(_FakeSession):
        def state_member_row(self):
            return {**super().state_member_row(), "position": truth}

    group.members[0].session = _Holding("BTC-USDT-SWAP", 1)
    early_exit_receipt = {"instId": "BTC-USDT-SWAP", "sessionId": "x", "runId": 1,
                          "state": "paused"}          # ← 没有 `position` 键

    rows = AiTradeManager._state_member_rows(group, [early_exit_receipt])
    assert rows[0]["position"] == truth

    real_receipt = {**early_exit_receipt, "position": None, "positionUnknown": True,
                    "backstop": None}
    rows = AiTradeManager._state_member_rows(group, [real_receipt])
    assert rows[0]["position"] is None and rows[0]["positionUnknown"] is True


# ---------------- L-1：派生态 vs 意图态 ----------------
async def test_group_state_is_derived_not_declared():
    """L-1：成员全 `stopped` 而 `group.state` 还写着 `running` ⇒ wire 里 `state` 必须是 `stopped`。

    `declaredState` 保留旧值，两者不等即 bug 的信号（也是本轮把 12 条根因串起来的那条线）。
    """
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group = _group("BTC-USDT-SWAP", "ETH-USDT-SWAP",
                       state=pause_core.STATUS_RUNNING)
        for member in group.members:
            member.session.state = "stopped"
            member.session.stop_reason = "session_stop_loss"
        manager._group = group

        wire = manager.group_status()
        assert wire["state"] == "stopped"
        assert wire["declaredState"] == "running"
        assert wire["lifecycle"]["armed"] is False
        assert wire["lifecycle"]["membersArmed"] == 0
        assert {row["reason"] for row in wire["lifecycle"]["notArmed"]} == {"session_stopped"}
    finally:
        _reset(manager)
        await dispose_engine()


async def test_one_running_member_keeps_the_group_running():
    """`derive_group_state` 的分组落点：任一 running ⇒ running（另一个死了只降 `armed`）。"""
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group = _group("BTC-USDT-SWAP", "ETH-USDT-SWAP",
                       state=pause_core.STATUS_RUNNING)
        group.members[0].session.state = "running"
        group.members[1].session.state = "stopped"
        group.members[1].session.stop_reason = "stale_feed"
        manager._group = group

        wire = manager.group_status()
        assert wire["state"] == "running"
        assert wire["lifecycle"]["armed"] is False
        assert wire["lifecycle"]["membersArmed"] == 1
        assert wire["lifecycle"]["membersTotal"] == 2
    finally:
        _reset(manager)
        await dispose_engine()


# ---------------- 用例 12（总闸）：一键逐字回滚 ----------------
async def test_the_master_switch_restores_the_previous_wire_byte_for_byte():
    """总闸关 ⇒ `state` 回落成「上一次动作赋的值」，`declaredState` / `lifecycle` **整键缺席**。"""
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        group = _group("BTC-USDT-SWAP", state=pause_core.STATUS_RUNNING)
        group.members[0].session.state = "stopped"
        group.members[0].session.executor.settings.ai_trade_lifecycle_truth_enabled = False
        manager._group = group

        wire = manager.group_status()
        assert wire["state"] == "running"              # 逐字回退到「意图」
        assert "declaredState" not in wire
        assert "lifecycle" not in wire
        assert "lifecycle" not in wire["members"][0]

        # 复检也不参与（不新增只在本轮才可能出现的失败态）。
        group.state = pause_core.STATUS_PAUSED
        group.members[0].session.state = "paused"
        await _seed_contract(group)
        await manager.resume_group("g-resume", acknowledged=True)
        assert group.state == pause_core.STATUS_RUNNING
    finally:
        _reset(manager)
        await dispose_engine()


# ---------------- 用例 11（D-12）：水合盲区必须对用户可见 ----------------
async def test_hydration_blind_spot_is_readable():
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        assert manager.is_hydration_blind() is False
        manager.note_hydration_failed()
        assert manager.is_hydration_blind() is True
    finally:
        _reset(manager)
        await dispose_engine()


# ---------------- I-11：冷路径复检未过时的回滚**绝不平仓** ----------------
async def test_cold_rollback_never_flattens_the_adopted_position(monkeypatch):
    """冷续跑复检未过 ⇒ 停会话、留契约，但**一单不平**。

    既有的 `resume_failed` 回滚（`resume.py` S7）用的是默认 `flatten=True`，因为那条路上
    `session.start()` 根本没起来。而本方法跑在**复检未过**时：此刻 `_adopt_group` 已经把交易所
    上那笔**真实持仓**灌进了 executor，照抄默认值会当场把它平掉 —— 那正是 [评审 R0-5] 要堵死的
    「点继续 → 几秒后被强平」，只是从另一扇门进来。
    """
    await init_db()
    manager = get_ai_trade_manager()
    _reset(manager)
    try:
        cold = _group("BTC-USDT-SWAP", group_id="g-cold")
        session = cold.members[0].session
        session.link_state = "down"                    # 复检必挂

        async def _fake_cold(_manager, _gid, *, acknowledged):
            return cold

        import app.ai_auto.resume as resume_mod
        monkeypatch.setattr(resume_mod, "resume_cold_group", _fake_cold)
        await _seed_contract(cold)

        with pytest.raises(AiTradeResumeNotArmed):
            await manager.resume_group("g-cold", acknowledged=True)

        assert session.state == "stopped"
        assert session.stop_reason == "resume_failed"
        assert session.on_stopped is None              # W5 已摘：契约不许被置 ended
        assert manager._group is None                  # 复检排在赋值之前，没有半成品分组留下
        async with get_sessionmaker()() as s:
            row = await state_repo.load_group_state(s, "g-cold")
        assert row["status"] == "paused"               # 契约留着，用户可以再试
    finally:
        _reset(manager)
        await dispose_engine()

