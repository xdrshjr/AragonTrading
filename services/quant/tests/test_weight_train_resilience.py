"""权重训练韧性测试（H-B1/R-02 落库失败兜底 + H-B4 关停 force_cancel）。

高风险路径用真实集成（真 manager + 真 WS 路由 + 临时 DB），仅注入 ``finalize_weight_run`` 的瞬态 / 持续
失败来逼出兜底语义；绝不 mock 状态机本身。
"""
from __future__ import annotations

import asyncio
import json

from app.api.weight_models import ws_weight_train
from app.db import repositories as repo
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.factors import service as factor_service
from app.factors import weight_model_service as wms
from app.factors.weight_train_manager import get_weight_train_manager
from app.okx.types import Candle
from app.schemas import WeightTrainStartReq
from tests.conftest import make_synthetic_candles

_RANGE = {"instId": "BTC-USDT-SWAP", "bar": "1H",
          "startTs": 1_700_000_000, "endTs": 1_700_000_000 + 295 * 3600}
_MEMBERS = [
    {"factorKey": "mom_roc", "params": {"win": 20}, "direction": "long"},
    {"factorKey": "trend_ema_dist", "params": {}, "direction": "long"},
    {"factorKey": "rev_rsi", "params": {}, "direction": "short"},
]


class _FakeRest:
    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        rows = make_synthetic_candles(min(limit, 300), start_ts=1_700_000_000, step=3600)
        return [Candle(ts=r["ts"], o=r["o"], h=r["h"], l=r["l"], c=r["c"], vol=r["vol"]) for r in rows]


class _GateRest(_FakeRest):
    """``get_candles`` 阻塞在 gate 上（永不放行）→ 模拟卡死作业，逼出关停 force_cancel 兜底（H-B4）。"""

    def __init__(self):
        self.entered = asyncio.Event()
        self._gate = asyncio.Event()

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        self.entered.set()
        await self._gate.wait()
        return await super().get_candles(inst_id, bar, limit=limit, before=before, after=after)


class _FakeWS:
    def __init__(self):
        self.sent: list = []
        self.closed = False

    async def accept(self):
        pass

    async def close(self):
        self.closed = True

    async def send_text(self, text):
        self.sent.append(json.loads(text))


def _req(**extra) -> WeightTrainStartReq:
    return WeightTrainStartReq(**{**_RANGE, "members": _MEMBERS, "method": "linear",
                                  "config": {"normWindow": 100}, "horizon": 6, **extra})


async def _start(rest) -> dict:
    factor_service.clear_candle_cache()
    sm = get_sessionmaker()
    async with sm() as s:
        return await wms.start_training(s, rest, _req())


async def _await_terminal(job_id: str, tries: int = 400) -> None:
    job = get_weight_train_manager().get(job_id)
    for _ in range(tries):
        if job is not None and job.is_terminal:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("作业未在限定时间内到达终态")


async def test_persist_transient_failure_retries_then_succeeds(monkeypatch):
    """H-B1/R-02：首次落库抛、重试即成功 → run 行终为 succeeded（有界重试生效）。"""
    await init_db()
    try:
        real = repo.finalize_weight_run
        calls = {"n": 0}

        async def flaky(session, run_id, *, outcome, run_cap):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("database is locked")   # 瞬态锁
            return await real(session, run_id, outcome=outcome, run_cap=run_cap)

        monkeypatch.setattr("app.db.repositories.finalize_weight_run", flaky)
        started = await _start(_FakeRest())
        await _await_terminal(started["jobId"])
        monkeypatch.undo()
        sm = get_sessionmaker()
        async with sm() as s:
            run = await wms.get_run(s, started["modelId"], started["runId"])
        assert run["status"] == "succeeded"   # 重试后落库成功
        assert calls["n"] >= 2                # 至少一次重试
    finally:
        await dispose_engine()


async def test_persist_persistent_failure_still_reaches_terminal(monkeypatch):
    """H-B1：持续落库失败 → WS 仍收终态 done 并关闭（不悬挂）、作业 is_terminal；run 行真实留 running（不假成功）。"""
    await init_db()
    try:
        async def always_fail(session, run_id, *, outcome, run_cap):
            raise RuntimeError("disk full")   # 硬故障

        monkeypatch.setattr("app.db.repositories.finalize_weight_run", always_fail)
        started = await _start(_FakeRest())
        ws = _FakeWS()
        await asyncio.wait_for(ws_weight_train(ws, started["jobId"]), timeout=10.0)   # 绝不悬挂
        types = [e["type"] for e in ws.sent]
        assert types[-1] == "done" and ws.closed
        job = get_weight_train_manager().get(started["jobId"])
        assert job is not None and job.is_terminal
        monkeypatch.undo()
        sm = get_sessionmaker()
        async with sm() as s:
            run = await wms.get_run(s, started["modelId"], started["runId"])
        assert run["status"] == "running"   # 诚实：DB 写确已失败，run 留 running 待启动对账改判
    finally:
        await dispose_engine()


async def test_load_empty_range_classified_as_empty_range(monkeypatch):
    """G-B2：数据加载抛 HTTPException(EMPTY_RANGE) → run failed/errorCode==EMPTY_RANGE，且 errorMessage
    绝不外泄 ``"400: {...}"`` Python dict repr（与同步 POST /factor-sets/train 干净 400 对齐）。"""
    await init_db()
    try:
        class _EmptyRest(_FakeRest):
            async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
                return []   # 区间内无 K 线 → load_range_candles 抛 400 EMPTY_RANGE

        started = await _start(_EmptyRest())
        await _await_terminal(started["jobId"])
        sm = get_sessionmaker()
        async with sm() as s:
            run = await wms.get_run(s, started["modelId"], started["runId"])
        assert run["status"] == "failed"
        assert run["errorCode"] == "EMPTY_RANGE"               # 真实码不再被吞成 WEIGHT_TRAIN_FAILED
        assert "400: {" not in (run["errorMessage"] or "")     # 不外泄 dict repr
    finally:
        await dispose_engine()


async def test_start_job_self_heals_on_start_failure(monkeypatch):
    """G-B6：start_job 中 job.start() 罕见抛 → 半注册回滚，active_count 不虚高（无永不终态的幽灵 running）。"""
    from app.factors import weight_train_manager as wtm
    from app.factors.service import FactorWindow
    manager = wtm.WeightTrainManager()
    assert manager.reserve()                                   # 预占 1

    def _boom(self):
        raise RuntimeError("no running event loop")

    monkeypatch.setattr(wtm._TrainJob, "start", _boom)
    spec = wtm.JobSpec(
        run_id=1, model_id=1, rest=None, members=[], method="linear", config={},
        window=FactorWindow(inst_id="BTC-USDT-SWAP", bar="1H", start_ts=1, end_ts=2, max_bars=None),
        horizon=6, run_cap=50, curve_steps=6)
    import pytest
    with pytest.raises(RuntimeError):
        manager.start_job(spec)
    assert manager.active_count() == 0       # 幽灵 job 已回滚，未计入并发
    assert manager._reserved == 0            # 预占已释放


async def test_shutdown_force_cancels_stuck_job(monkeypatch):
    """H-B4：卡死作业在关停时带超时 join，超时 force_cancel 兜底，run 终为 cancelled，关停不被无限拖。"""
    await init_db()
    try:
        monkeypatch.setattr("app.factors.weight_train_manager._SHUTDOWN_JOIN_TIMEOUT", 0.2)
        rest = _GateRest()
        started = await _start(rest)
        await asyncio.wait_for(rest.entered.wait(), timeout=2.0)   # 作业卡在 _load_df
        manager = get_weight_train_manager()
        await asyncio.wait_for(manager.shutdown(), timeout=5.0)    # 超时 force_cancel 仍落终态
        sm = get_sessionmaker()
        async with sm() as s:
            run = await wms.get_run(s, started["modelId"], started["runId"])
        assert run["status"] == "cancelled"
    finally:
        await dispose_engine()
