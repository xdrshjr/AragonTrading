"""AiBacktestManager + _BacktestJob 单测（ai-auto-trading-backtest §2.6/§2.9 / §7.1）。

并发闸（第 `max_jobs+1` 个 start→429）；前置门禁（区间/轮数越界）；get/cancel 生命周期；`_BacktestJob`
订阅回放（snapshot_events 回放缓冲的 phase/progress/决策 + 终态后 result + 终态事件）。作业协程打桩为
空转（不触网、不真跑），仅验证注册表与闸逻辑。
"""
from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import select

from app.ai_auto.backtest import AiBacktestRunner, _BacktestJob
from app.ai_auto.backtest_manager import (
    AiBacktestError,
    AiBacktestStartRequest,
    get_ai_backtest_manager,
)
from app.config import Settings
from app.db import repositories as repo
from app.db.models import Strategy
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.paper.strategy_factory import build_paper_strategy_spec


async def _idle_run(self, job):
    """作业协程桩：空转直到被取消（保持 job 非终态，供并发闸计数）。"""
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        job.mark_terminal("cancelled", {"jobId": job.job_id})
        raise


async def _seed_spec():
    await init_db()
    async with get_sessionmaker()() as s:
        sid = (await s.execute(select(Strategy.id).order_by(Strategy.id.asc()).limit(1))).scalar_one()
        row = await repo.get_strategy_row(s, sid)
    return build_paper_strategy_spec(row)


def _req(spec, *, every=None, start_off=100_000, end_off=1_000) -> AiBacktestStartRequest:
    now = int(time.time())
    return AiBacktestStartRequest(
        specs=[spec], inst_id="BTC-USDT-SWAP", bar="1H", start_ts=now - start_off,
        end_ts=now - end_off, settings=Settings(ai_backtest_max_jobs=2, ai_backtest_max_rounds=400),
        context_refresh_rounds=10, decision_every_bars=every)


def _reset_manager():
    mgr = get_ai_backtest_manager()
    mgr._jobs.clear()
    mgr._reserved = 0
    return mgr


async def _teardown(mgr):
    for job in list(mgr._jobs.values()):
        job.force_cancel()
        await job.join()
    mgr._jobs.clear()
    mgr._reserved = 0


async def test_concurrency_gate_rejects_over_cap(monkeypatch):
    monkeypatch.setattr("app.ai_auto.backtest_manager.resolve_active_provider",
                        lambda *_a, **_k: _async_obj())
    monkeypatch.setattr("app.ai_auto.backtest.make_llm_client_from_config", lambda cfg: object())
    monkeypatch.setattr(AiBacktestRunner, "run", _idle_run)
    mgr = _reset_manager()
    try:
        spec = await _seed_spec()
        j1 = await mgr.start(_req(spec, every=100))
        j2 = await mgr.start(_req(spec, every=100))
        assert j1.job_id != j2.job_id
        with pytest.raises(AiBacktestError) as exc:      # 第 3 个越并发闸
            await mgr.start(_req(spec, every=100))
        assert exc.value.code == "AI_BACKTEST_BUSY"
        assert exc.value.status == 429
    finally:
        await _teardown(mgr)
        await dispose_engine()


async def test_start_rejects_too_many_rounds(monkeypatch):
    monkeypatch.setattr("app.ai_auto.backtest_manager.resolve_active_provider",
                        lambda *_a, **_k: _async_obj())
    mgr = _reset_manager()
    try:
        spec = await _seed_spec()
        # 30 天 1H ≈ 720 根，间隔 1 → 720 轮 > 400 上限
        with pytest.raises(AiBacktestError) as exc:
            await mgr.start(_req(spec, every=1, start_off=30 * 24 * 3600, end_off=0))
        assert exc.value.code == "AI_BACKTEST_TOO_MANY_ROUNDS"
        assert exc.value.status == 400
    finally:
        await _teardown(mgr)
        await dispose_engine()


async def test_start_rejects_invalid_range(monkeypatch):
    monkeypatch.setattr("app.ai_auto.backtest_manager.resolve_active_provider",
                        lambda *_a, **_k: _async_obj())
    mgr = _reset_manager()
    try:
        spec = await _seed_spec()
        req = _req(spec, every=100)
        req.start_ts, req.end_ts = req.end_ts, req.start_ts   # 起止倒置
        with pytest.raises(AiBacktestError) as exc:
            await mgr.start(req)
        assert exc.value.code == "AI_BACKTEST_RANGE_INVALID"
    finally:
        await _teardown(mgr)
        await dispose_engine()


async def test_get_and_cancel_lifecycle(monkeypatch):
    monkeypatch.setattr("app.ai_auto.backtest_manager.resolve_active_provider",
                        lambda *_a, **_k: _async_obj())
    monkeypatch.setattr("app.ai_auto.backtest.make_llm_client_from_config", lambda cfg: object())
    monkeypatch.setattr(AiBacktestRunner, "run", _idle_run)
    mgr = _reset_manager()
    try:
        spec = await _seed_spec()
        job = await mgr.start(_req(spec, every=100))
        assert mgr.get(job.job_id) is job
        assert mgr.cancel(job.job_id) == "cancelling"
        assert mgr.cancel("nope") == "unknown"
    finally:
        await _teardown(mgr)
        await dispose_engine()


def test_backtest_job_snapshot_replays_buffered_events():
    """_BacktestJob 订阅回放：snapshot_events 含 job/decisions/summaries，终态后含 result + 终态事件。"""
    job = _BacktestJob("aibt_snap", _DummyRunner())
    job.publish({"type": "phase", "data": {"phase": "running"}})
    job.publish({"type": "progress", "data": {"barIndex": 3, "totalBars": 10, "pct": 0.3,
                                              "decisionRound": 1, "ts": 1, "equity": 10000.0}})
    job.publish({"type": "decision", "data": {"round": 1, "action": "hold"}})
    job.publish({"type": "summary", "data": {"round": 1, "ts": 1, "summary": "s"}})
    snap = job.snapshot_events()
    assert snap[0]["type"] == "snapshot"
    assert snap[0]["data"]["job"]["phase"] == "running"
    assert len(snap[0]["data"]["decisions"]) == 1
    assert len(snap[0]["data"]["summaries"]) == 1
    # 终态后回放含 result + 终态事件
    job.publish({"type": "result", "data": {"runId": 5, "metrics": {}}})
    job.mark_terminal("completed", {"jobId": job.job_id, "runId": 5})
    snap2 = job.snapshot_events()
    types = [e["type"] for e in snap2]
    assert "result" in types and types[-1] == "done"
    assert job.is_terminal and job.run_id == 5


class _DummyRunner:
    """_BacktestJob 单测用最小 runner 桩（只需 status_dict 读到的字段）。"""

    inst_id = "BTC-USDT-SWAP"
    bar = "1H"
    strategy_ids = [1]
    start_ts = 0
    end_ts = 1
    planned_rounds = 3


async def _async_obj():
    return object()


async def test_profit_rule_is_forwarded_to_spec(monkeypatch):
    """[adaptive P1-1] `AiBacktestStartRequest.profit_rule` 必须透传到 `AiBacktestSpec`。

    不透传就是**整条断路**：API 层解析出的规则到不了 runner，F3 全部代码都在但永不生效
    （跑不出错、功能静默缺失）——这类缺陷只有守门用例逮得到。
    """
    from app.ai_auto.profit_guard import ProfitGuardRule

    monkeypatch.setattr("app.ai_auto.backtest_manager.resolve_active_provider",
                        lambda *_a, **_k: _async_obj())
    monkeypatch.setattr("app.ai_auto.backtest.make_llm_client_from_config",
                        lambda cfg: object())
    spec = await _seed_spec()
    mgr = _reset_manager()
    monkeypatch.setattr(AiBacktestRunner, "run", _idle_run)
    rule = ProfitGuardRule(giveback_pct=0.25, cooldown_s=120.0)
    try:
        req = _req(spec)
        req.profit_rule = rule
        job = await mgr.start(req)
        assert job.runner.profit_rule is rule

        default_job = await mgr.start(_req(spec))       # 默认仍是 None（既有直构造零回归）
        assert default_job.runner.profit_rule is None
    finally:
        await _teardown(mgr)
        await dispose_engine()
