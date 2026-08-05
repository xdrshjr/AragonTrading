"""发现循环运行时测试（strategy-factor-discovery §7.1 / §2.9）。

覆盖：单轮四步顺序与落库、必达终态、落库失败仍达终态、协作式取消、
数据不可达 → ``paused(data_unavailable)`` 且**绝不合成数据**、事件形状与环形缓冲、
LLM 未配置时循环照常推进（fail-open）。

OKX 全程打桩（``load_range_candles`` 被替换），不触网。
"""
from __future__ import annotations

import asyncio
import json
import math

import pytest
from fastapi import HTTPException
from sqlalchemy import delete

from app.config import Settings
# ⚠️ 同名两类：``db.models.DiscoveryRun`` 是 ORM 行，``discovery.runtime.DiscoveryRun`` 是运行时
# 对象（设计文档对两者用了同一个名字）。同文件同时用到时必须显式起别名，否则
# ``delete(DiscoveryRun)`` 会拿到运行时类并抛 ``ArgumentError``。
from app.db.models import DiscoveryCandidate, DiscoveryIteration
from app.db.models import DiscoveryRun as DiscoveryRunRow
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.discovery import repositories as repo
from app.discovery import runtime as runtime_module
from app.discovery.runtime import DiscoveryRun, RunSpec
from app.okx.rest_client import OKXError
from app.okx.types import Candle

_TS = 1_700_000_000


def _candles(n: int = 900) -> list[Candle]:
    """确定性 K 线：前瞻收益由成交量信号驱动，保证真的能挖出东西（而不是空跑）。"""
    out: list[Candle] = []
    price = 100.0
    for i in range(n):
        sig = math.sin(i / 5.0)
        close = price * (1.0 + 0.012 * sig)
        out.append(Candle(ts=_TS + i * 3600, o=round(price, 4),
                          h=round(max(price, close) * 1.001, 4),
                          l=round(min(price, close) * 0.999, 4),
                          c=round(close, 4), vol=1000.0 * (1.0 + 0.6 * sig)))
        price = close
    return out


def _settings(**over) -> Settings:
    base = {"discovery_candidate_timeout_s": 20.0, "discovery_iteration_timeout_s": 600.0,
            "discovery_validate_concurrency": 2, "discovery_ws_ring": 50,
            "discovery_max_candidate_rows": 500, "discovery_max_iteration_rows": 50,
            "discovery_llm_max_calls_per_hour": 0}
    base.update(over)
    return Settings(**base)


def _config(**over) -> dict:
    base = {"windowBars": 900, "axes": ["factor"], "horizon": 1, "isRatio": 0.7,
            "candidatesPerIteration": 6, "iterationIntervalS": 60.0, "dataRefreshS": 0.0,
            "windowMode": "rolling", "maxIterations": 1, "maxRuntimeHours": 0.0,
            "maxKept": 50, "autoResume": True, "autoPromote": False,
            "revalidateOnRefresh": True, "seed": 20260805,
            "llm": {"enabled": False, "reviewSample": 0},
            "thresholds": {}, "evolution": {"coldStartMin": 5, "adaptEvery": 10,
                                            "fsaEvery": 25, "fsaBanTtlIters": 150,
                                            "variantCap": 6}}
    base.update(over)
    return base


async def _fresh():
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        await session.execute(delete(DiscoveryCandidate))
        await session.execute(delete(DiscoveryIteration))
        await session.execute(delete(DiscoveryRunRow))
        await session.commit()
    return sm


async def _make_run(sm, *, config: dict | None = None, resume: bool = False,
                    settings: Settings | None = None) -> DiscoveryRun:
    cfg = config or _config()
    async with sm() as session:
        row = await repo.create_run(session, values=repo.RunValues(
            label="BTC 1H 挖掘", inst_id="BTC-USDT-SWAP", bar="1H",
            symbols=["BTC-USDT-SWAP"], config=cfg,
            checkpoint={"iteration": 0, "rngSeed": cfg["seed"]}, started_ts=_TS))
    return DiscoveryRun(RunSpec(
        run_id=int(row.id), inst_id="BTC-USDT-SWAP", bar="1H", symbols=("BTC-USDT-SWAP",),
        config=cfg, rest=object(), settings=settings or _settings(), resume=resume,
        llm_client=None))


def _patch_candles(monkeypatch, candles=None, error: Exception | None = None) -> None:
    async def _load(rest, **kwargs):
        if error is not None:
            raise error
        return candles if candles is not None else _candles()

    monkeypatch.setattr(runtime_module, "load_range_candles", _load)


async def _drive(run: DiscoveryRun, *, timeout: float = 60.0) -> None:
    run.start()
    await asyncio.wait_for(run.join(), timeout=timeout)


# ============================ 单轮四步 + 必达终态 ============================
async def test_one_iteration_writes_candidates_and_reaches_terminal(monkeypatch):
    sm = await _fresh()
    _patch_candles(monkeypatch)
    try:
        run = await _make_run(sm)
        await _drive(run)
        assert run.status == "succeeded" and run.is_terminal
        async with sm() as session:
            rows, total = await repo.list_candidates(session, run.run_id, verdict=None)
            iterations = await repo.list_iterations(session, run.run_id)
            row = await repo.get_run(session, run.run_id)
        assert total > 0, "一轮下来一条候选都没落库，循环等于空转"
        assert [i.idx for i in iterations] == [1]
        stats = json.loads(iterations[0].stats_json)
        assert stats["generated"] > 0 and stats["validated"] >= 0
        assert row.iteration == 1 and row.status == "succeeded"
        assert json.loads(row.checkpoint_json)["iteration"] == 1
    finally:
        await dispose_engine()


async def test_events_are_emitted_with_state_first_and_done_last(monkeypatch):
    sm = await _fresh()
    _patch_candles(monkeypatch)
    try:
        run = await _make_run(sm)
        await _drive(run)
        snapshot = run.snapshot()
        assert snapshot[0]["type"] == "state"
        assert snapshot[-1]["type"] == "done"
        types = {event["type"] for event in snapshot}
        assert {"state", "iteration", "done"} <= types
        state = snapshot[0]["data"]
        assert set(state) >= {"runId", "status", "iteration", "counters", "budget",
                              "poolSizes", "mechanismCoverage", "fsaBans",
                              "nextIterationTs", "dataWindow", "llmUsage", "rebuilding"}
    finally:
        await dispose_engine()


async def test_ring_buffer_is_bounded_but_terminal_event_survives(monkeypatch):
    sm = await _fresh()
    _patch_candles(monkeypatch)
    try:
        run = await _make_run(sm, settings=_settings(discovery_ws_ring=50))
        await _drive(run)
        for i in range(200):
            run._push({"type": "candidate", "data": {"i": i}})
        snapshot = run.snapshot()
        assert len(snapshot) <= 50 + 2          # state 头 + 环形 + done 尾
        assert snapshot[-1]["type"] == "done"   # 终态永远不被挤掉
    finally:
        await dispose_engine()


# ============================ 数据不可达（绝不合成数据） ============================
@pytest.mark.parametrize("error", [
    OKXError("OKX_UNREACHABLE", "network down"),
    HTTPException(400, detail={"code": "EMPTY_RANGE", "message": "no candles"}),
    asyncio.TimeoutError(),
    OSError("socket reset"),
])
async def test_data_failure_pauses_instead_of_crashing(monkeypatch, error):
    """捕获面必须覆盖四类异常。只捕 ``OKXError`` 会让整个 run 崩成 ``failed``（评审 P2-2）。"""
    sm = await _fresh()
    _patch_candles(monkeypatch, error=error)
    try:
        run = await _make_run(sm)
        await _drive(run)
        assert run.status == "paused"
        assert run.pause_reason == "data_unavailable"
        async with sm() as session:
            row = await repo.get_run(session, run.run_id)
            _rows, total = await repo.list_candidates(session, run.run_id, verdict=None)
        assert row.status == "paused" and row.pause_reason == "data_unavailable"
        assert total == 0, "取数失败却产出了候选 ⇒ 用了合成数据（硬约束被破坏）"
    finally:
        await dispose_engine()


async def test_data_failure_emits_an_error_event_without_terminating_the_stream(monkeypatch):
    """``paused`` 绝不推 ``done``。

    ``api/discovery.py`` 见 ``done`` 即关流，而 ``snapshot()`` 把终态事件永久钉在缓冲尾部——
    一条还能恢复的任务会因此变成「怎么连都立刻断」的死流。
    """
    sm = await _fresh()
    _patch_candles(monkeypatch, error=OKXError("OKX_UNREACHABLE", "down"))
    try:
        run = await _make_run(sm)
        await _drive(run)
        types = [event["type"] for event in run.snapshot()]
        assert "error" in types
        error_event = next(e for e in run.snapshot() if e["type"] == "error")
        assert error_event["data"]["code"] == "DISCOVERY_DATA_UNAVAILABLE"
        assert "done" not in types, "暂停不是终态，推 done 会让恢复后的实时流永远连不上"
        assert run.snapshot()[-1]["type"] != "done"
    finally:
        await dispose_engine()


async def test_pause_reflects_in_the_status_projection_and_resume_restores_it(monkeypatch):
    """暂停必须同步改写 ``status``。

    ``service.run_summary`` 以内存态优先：只置内部标志会让任务列表与 WS ``state`` 一直报
    「挖掘中」，而 DB 行早已是 ``paused``——用户点了暂停却看不出暂停。
    """
    sm = await _fresh()
    _patch_candles(monkeypatch)
    try:
        run = await _make_run(sm, config=_config(maxIterations=0))
        run.request_pause("user")
        assert run.status == "paused" and run.state_snapshot()["status"] == "paused"
        assert run.snapshot()[-1]["data"]["status"] == "paused"
        run.request_resume()
        assert run.status == "running" and run.state_snapshot()["pauseReason"] == ""
    finally:
        await dispose_engine()


async def test_pause_during_prepare_leaves_a_dead_loop_that_reports_itself(monkeypatch):
    """``_prepare`` 阶段取数失败转 ``paused`` 后循环已退出 ⇒ ``is_alive`` 必须为假。

    服务层据此重新装配 run；若照旧只翻标志，接口会回 ``running`` 而实际什么都没跑。
    """
    sm = await _fresh()
    _patch_candles(monkeypatch, error=OKXError("OKX_UNREACHABLE", "down"))
    try:
        run = await _make_run(sm)
        await _drive(run)
        assert run.status == "paused" and not run.is_terminal
        assert not run.is_alive
        run.request_resume()
        assert not run.is_alive, "循环协程已返回，翻标志不可能把它救活"
    finally:
        await dispose_engine()


async def test_refresh_rebuilds_the_dedupe_pool_even_when_revalidation_is_off(monkeypatch):
    """``revalidateOnRefresh=false`` 只关「复评淘汰」，绝不关「重建基准」。

    窗口右移后 kept 池的值序列与新窗口不再等长，F10 的值通道会逐候选抛 ValueError 并被吞成
    ``eval_error``——表现为「刷新之后突然一条都收不到」，而不是报错。
    """
    sm = await _fresh()
    _patch_candles(monkeypatch)
    calls: list[bool] = []
    try:
        run = await _make_run(sm, config=_config(dataRefreshS=1.0,
                                                 revalidateOnRefresh=False))
        assert await run._prepare()
        original = DiscoveryRun._rebuild_pool

        async def _spy(self, *, demote: bool = True):
            calls.append(demote)
            return await original(self, demote=demote)

        monkeypatch.setattr(DiscoveryRun, "_rebuild_pool", _spy)
        run._checkpoint.data_window["lastRefreshTs"] = 0      # 强制刷新到点
        assert await run._ensure_fresh_data()
        assert calls == [False], f"刷新时未重建去重基准（实际调用：{calls}）"
    finally:
        await dispose_engine()


# ============================ 落库失败仍达终态 ============================
async def test_persist_failure_still_reaches_a_terminal_state(monkeypatch):
    """落库失败绝不让 WS 永久悬挂、run 永久卡 running（H-B1 教训）。"""
    sm = await _fresh()
    _patch_candles(monkeypatch)

    async def _boom(*args, **kwargs):
        raise RuntimeError("db locked")

    monkeypatch.setattr(runtime_module.repo, "commit_iteration", _boom)
    try:
        run = await _make_run(sm)
        await _drive(run)
        assert run.status == "failed" and run.is_terminal
        assert run.snapshot()[-1]["type"] == "done"
        async with sm() as session:
            row = await repo.get_run(session, run.run_id)
        assert row.status == "failed" and row.error_code == "DISCOVERY_FAILED"
    finally:
        await dispose_engine()


# ============================ 协作式取消 ============================
async def test_cancel_before_first_iteration_reaches_stopped(monkeypatch):
    sm = await _fresh()
    _patch_candles(monkeypatch)
    try:
        run = await _make_run(sm, config=_config(maxIterations=0))
        run.start()
        run.request_cancel()
        await asyncio.wait_for(run.join(), timeout=60.0)
        assert run.status == "stopped" and run.is_terminal
    finally:
        await dispose_engine()


async def test_cancel_during_the_inter_iteration_sleep(monkeypatch):
    """取消在两轮之间的等待期立即生效（不必等满一个 60s 间隔）。"""
    sm = await _fresh()
    _patch_candles(monkeypatch)
    try:
        run = await _make_run(sm, config=_config(maxIterations=0, iterationIntervalS=60.0))
        run.start()
        for _ in range(600):                 # 等第一轮落库完成
            await asyncio.sleep(0.05)
            if run._checkpoint.iteration >= 1:
                break
        assert run._checkpoint.iteration >= 1
        run.request_cancel()
        await asyncio.wait_for(run.join(), timeout=10.0)
        assert run.status == "stopped"
    finally:
        await dispose_engine()


# ============================ 暂停 / 恢复 ============================
async def test_pause_holds_the_loop_and_resume_releases_it(monkeypatch):
    sm = await _fresh()
    _patch_candles(monkeypatch)
    try:
        run = await _make_run(sm, config=_config(maxIterations=0))
        run.request_pause("user")
        run.start()
        await asyncio.sleep(0.3)
        assert run._checkpoint.iteration == 0     # 暂停中不推进
        assert not run.is_terminal
        run.request_cancel()
        await asyncio.wait_for(run.join(), timeout=10.0)
        assert run.status == "stopped"
    finally:
        await dispose_engine()


# ============================ LLM 未配置时照常推进（fail-open） ============================
async def test_loop_runs_without_any_llm(monkeypatch):
    """DoD #9：LLM 未配置时任务照常推进，入库标准不变。"""
    sm = await _fresh()
    _patch_candles(monkeypatch)
    try:
        run = await _make_run(sm)
        assert run._llm.enabled is False
        await _drive(run)
        assert run.status == "succeeded"
        async with sm() as session:
            iterations = await repo.list_iterations(session, run.run_id)
        llm = json.loads(iterations[0].llm_json)
        assert llm["calls"] == 0
        # LLM 份额转给 random：生成数不因缺 LLM 而缩水。
        stats = json.loads(iterations[0].stats_json)
        assert stats["generated"] > 0
    finally:
        await dispose_engine()


# ============================ 迭代记录如实上报 ============================
async def test_iteration_note_reports_truncation_and_overrun_honestly(monkeypatch):
    from app.discovery.iteration_stats import IterationTally, iteration_note, tally_to_stats

    tally = IterationTally(generated=40, validated=12, kept=2,
                           truncated_by_timeout=28, overrun=True)
    note = iteration_note(tally)
    assert "丢弃 28 条" in note and "超过迭代间隔" in note
    stats = tally_to_stats(tally)
    assert stats["truncatedByTimeout"] == 28 and stats["overrun"] is True
    _ = monkeypatch
