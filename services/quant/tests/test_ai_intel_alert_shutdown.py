"""告警回写任务的关停收敛（ai-trade-intel-actionability G-26）。

本轮**不新增任何关停步骤**：台账回写走 `intel_scheduler.schedule(...)` 这个既有 tracked 任务集，
它已经被 `main._shutdown_steps()` 的第 7 步 `ai_trade_intel` drain 掉。这条纪律要被两侧同时钉住
——任务真的收敛（本文件），以及步骤表没有偷偷长出第 15 步（`test_main_shutdown.py`）。
"""
from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import delete

from app.ai_auto.intel import runtime as intel_runtime
from app.ai_auto.intel import scheduler as intel_scheduler
from app.ai_auto.intel import service
from app.config import Settings
from app.db import repositories as repo
from app.db.models import AiIntelAlert
from app.db.session import get_sessionmaker, init_db

INST = "BTC-USDT-SWAP"
GROUP = "grp-shutdown"


async def _fresh() -> None:
    await init_db()
    async with get_sessionmaker()() as session:
        await session.execute(delete(AiIntelAlert))
        await session.commit()


def _runtime() -> intel_runtime.IntelRuntime:
    deps = service.IntelDeps(settings=Settings(), sessionmaker=get_sessionmaker,
                             group_id=GROUP, now=time.time)
    return intel_runtime.IntelRuntime(GROUP, [INST], deps, enabled=True)


async def _seed_alert() -> int:
    async with get_sessionmaker()() as session:
        return await repo.write_intel_alert(session, {
            "instId": INST, "itemId": 1, "createdTs": int(time.time()),
            "fired": True, "severity": 0.9, "headline": "h"})


@pytest.mark.asyncio
async def test_the_sync_writeback_lands_through_the_existing_tracked_task_set():
    """`note_alert_outcome` 是**同步函数**：会话侧的两个调用点都在同步上下文里，一个协程在
    那里根本 await 不了（评审 P1-10）。它把写段交给既有 tracked 任务集，任务跑完即落库。"""
    await _fresh()
    runtime = _runtime()
    intel_runtime.register(runtime)
    try:
        alert_id = await _seed_alert()
        intel_runtime.note_alert_outcome(INST, alert_id, outcome="woke", run_id=3,
                                         round_no=9)
        pending = set(intel_scheduler.tasks())
        assert pending, "schedule 之后必须有一个在飞的 tracked 任务"
        await asyncio.gather(*pending)
        assert not intel_scheduler.tasks(), "done_callback 必须把自己摘掉（防泄漏）"
        async with get_sessionmaker()() as session:
            rows, _ = await repo.list_intel_alerts(session, inst_id=INST)
        assert rows[0]["outcome"] == "woke" and rows[0]["runId"] == 3
    finally:
        intel_runtime.unregister(GROUP)


@pytest.mark.asyncio
async def test_a_writeback_in_flight_converges_under_drain_and_never_raises():
    """G-26：起一条回写任务后**立刻** drain ⇒ 任务集收敛为空且不抛。

    `drain` 的语义是「**先 cancel 全部、再有界收敛**」，故这一条**不断言那行写进去了**——
    被 cancel 掉的回写留下一行 `outcome == ""`，它由动态页签按 TTL 渲染成
    「会话未在时限内响应」（**渲染期判定，不回写**，R-10 的成文取舍）。
    """
    await _fresh()
    runtime = _runtime()
    intel_runtime.register(runtime)
    try:
        alert_id = await _seed_alert()
        intel_runtime.note_alert_outcome(INST, alert_id, outcome="woke")
        assert intel_scheduler.tasks()
        await intel_scheduler.drain(5.0)
        assert not intel_scheduler.tasks()
    finally:
        intel_runtime.unregister(GROUP)


@pytest.mark.asyncio
async def test_a_writeback_without_a_runtime_is_a_no_op():
    """会话已停 / 回测车道 ⇒ 运行时不在 ⇒ **no-op**，绝不建任务、绝不抛。"""
    await _fresh()
    intel_runtime.note_alert_outcome("NEVER-SEEN-SWAP", 1, outcome="woke")
    intel_runtime.note_alert_outcome(INST, 0, outcome="woke")
    assert not intel_scheduler.tasks()


@pytest.mark.asyncio
async def test_a_failing_writeback_is_swallowed_into_a_log_line():
    """台账是加法：一次失败的回写只放弃这一次，绝不冒泡成「Task exception was never retrieved」。"""
    await _fresh()

    def _boom():
        raise RuntimeError("db gone")

    deps = service.IntelDeps(settings=Settings(), sessionmaker=_boom, group_id=GROUP,
                             now=time.time)
    runtime = intel_runtime.IntelRuntime(GROUP, [INST], deps, enabled=True)
    intel_runtime.register(runtime)
    try:
        intel_runtime.note_alert_outcome(INST, 1, outcome="woke")
        await intel_scheduler.drain(5.0)
        assert not intel_scheduler.tasks()
    finally:
        intel_runtime.unregister(GROUP)


@pytest.mark.asyncio
async def test_an_unarmed_runtime_shell_never_hands_off_an_alert():
    """`enabled=False` 的壳不起任何后台任务，也不该产生任何告警（§3.5 最后一条）。"""
    await _fresh()
    seen: list = []
    deps = service.IntelDeps(settings=Settings(), sessionmaker=get_sessionmaker,
                             group_id=GROUP, now=time.time)
    shell = intel_runtime.IntelRuntime(GROUP, [INST], deps, enabled=False,
                                       notify_alert=lambda inst, h: seen.append(h))
    shell._alert(INST, object())
    assert seen == []


@pytest.mark.asyncio
async def test_the_static_summary_keeps_the_new_keys_in_shape():
    """G-25：未装配的分组也必须**形状一致**——前端读到 `undefined` 会把「没装上」画成「零告警」。"""
    summary = intel_runtime._static_summary(INST, code="no_provider")
    assert summary["alerts24h"] == 0
    assert summary["lastAlertTs"] is None
    assert summary["efficacy"]["enabled"] is False
    assert summary["efficacy"]["overall"]["accuracy"] is None
    assert set(summary["efficacy"]) == {"enabled", "kMin", "sampleN", "overall"}


@pytest.mark.asyncio
async def test_the_alert_hop_runs_after_refresh_not_before():
    """顺序不可换：`ref` 与 `in_digest` 都要从**刚刷新的** digest 反查（评审 P0-3）。"""
    import inspect

    src = inspect.getsource(intel_runtime.IntelRuntime._after_round)
    assert src.index("await self.refresh(") < src.index("self._alert(")


@pytest.mark.asyncio
async def test_in_digest_is_false_when_the_item_missed_the_lines():
    """「有编号」≠「在 prompt 里」：判据取定稿那一刻交出来的 id 集合。

    第三跳还要把这两样**补写进台账**：它们在建行那一刻还不知道（那时 digest 是上一轮的），
    而动态页签的「未进入情报段」与 `[Ixx]` 前缀都只读它们。少了那次补写，两列就是「有列、
    有读点、没有写点」——界面会给每一条告警都挂上一句关于数据的假话。
    """
    await _fresh()
    from app.ai_auto.intel.alert import AlertCandidate, AlertHandoff

    seen: list = []
    runtime = _runtime()
    runtime._notify_alert = lambda inst, handoff: seen.append(handoff)
    missed_id = await _seed_alert()
    runtime._digest[INST] = {"lines": ["x"], "hidden": 3, "refs": {"I01": 99},
                             "citeEnabled": True}
    runtime._digest_ids[INST] = frozenset({99})
    candidate = AlertCandidate(item_id=41, headline="h", category="risk", tier="ultra",
                               stance="bearish", impact="high", confidence=0.8,
                               event_ts=0, captured_ts=0, domain_count=3, quoted=True,
                               is_scheduled=False, verdict="", summary="s")
    runtime._alert(INST, AlertHandoff(alert_id=missed_id, candidate=candidate,
                                      severity=0.9, created_wall=time.time()))
    assert len(seen) == 1
    assert seen[0].in_digest is False and seen[0].ref == ""
    # 反过来：它真的在里面时，`ref` 与 `in_digest` 一起为真。
    hit_id = await _seed_alert()
    runtime._digest[INST] = {"lines": ["x"], "hidden": 0, "refs": {"I01": 41},
                             "citeEnabled": True}
    runtime._digest_ids[INST] = frozenset({41})
    runtime._alert(INST, AlertHandoff(alert_id=hit_id, candidate=candidate, severity=0.9,
                                      created_wall=time.time()))
    assert seen[-1].in_digest is True and seen[-1].ref == "I01"
    # 两次补写都走既有 tracked 任务集（本轮**不新增关停步骤**），跑完即落库。
    await asyncio.gather(*set(intel_scheduler.tasks()))
    async with get_sessionmaker()() as session:
        missed = await session.get(AiIntelAlert, missed_id)
        hit = await session.get(AiIntelAlert, hit_id)
        assert missed.in_digest is False and missed.ref_token == ""
        assert hit.in_digest is True and hit.ref_token == "I01"
        # 补写只碰这两列：`outcome` 那一组仍是建行时的空值（同一行三个写点各写各的列）。
        assert hit.outcome == "" and hit.run_id == 0


@pytest.mark.asyncio
async def test_pending_writebacks_do_not_survive_into_a_second_event_loop():
    """收尾护栏：本文件跑完之后不能给别的用例留下在飞任务（它们共用一个临时库）。"""
    await asyncio.sleep(0)
    assert not intel_scheduler.tasks()
