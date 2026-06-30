"""权重训练 manager 并发与淘汰测试（H-B3 硬上限不击穿 + H-B6 假时钟 TTL/数量淘汰）。

并发击穿走真实 ``start_training``（真 manager + 临时 DB + 门控假 OKX）；TTL/数量淘汰用注入假时钟直接
驱动 ``_prune_finished``（确定性，无需真实久等）。
"""
from __future__ import annotations

import asyncio

from fastapi import HTTPException
from sqlalchemy import delete

from app.config import get_settings
from app.db.models import WeightModel, WeightTrainRun
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.factors import service as factor_service
from app.factors import weight_model_service as wms
import app.factors.weight_train_manager as wtm
from app.factors.service import FactorWindow
from app.factors.weight_train_manager import (
    _FINISHED_JOBS_CAP,
    JobSpec,
    WeightTrainManager,
    _TrainJob,
    get_weight_train_manager,
)
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


class _GateRest:
    """``get_candles`` 阻塞在 gate 上 → 作业占住 running 名额，使并发上限可被确定性检验。"""

    def __init__(self):
        self.gate = asyncio.Event()

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        await self.gate.wait()
        rows = make_synthetic_candles(min(limit, 300), start_ts=1_700_000_000, step=3600)
        return [Candle(ts=r["ts"], o=r["o"], h=r["h"], l=r["l"], c=r["c"], vol=r["vol"]) for r in rows]


def _req(**extra) -> WeightTrainStartReq:
    return WeightTrainStartReq(**{**_RANGE, "members": _MEMBERS, "method": "linear",
                                  "config": {"normWindow": 100}, "horizon": 6, **extra})


def _job_spec() -> JobSpec:
    return JobSpec(run_id=1, model_id=1, rest=None, members=[], method="linear", config={},
                   window=FactorWindow(inst_id="X", bar="1H", start_ts=0, end_ts=1),
                   horizon=6, run_cap=50, curve_steps=6)


async def _await_terminal(job_id: str, tries: int = 400) -> None:
    job = get_weight_train_manager().get(job_id)
    for _ in range(tries):
        if job is not None and job.is_terminal:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("作业未在限定时间内到达终态")


async def test_concurrency_hard_cap_not_breached():
    """H-B3：并发发起 cap+1 个 start_training，恰好 1 个 429，实际注册作业数 == cap，被拒方零落库。"""
    await init_db()
    settings = get_settings()
    prev_cap, settings.max_weight_train_jobs = settings.max_weight_train_jobs, 2
    wtm._manager = None   # 全新单例隔离，确保起点 0 个在跑作业
    factor_service.clear_candle_cache()
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(delete(WeightTrainRun))
        await s.execute(delete(WeightModel))
        await s.commit()
    rest = _GateRest()
    try:
        async def attempt():
            async with sm() as s:
                try:
                    return await wms.start_training(s, rest, _req())
                except HTTPException as exc:
                    return exc.status_code

        results = await asyncio.gather(*[attempt() for _ in range(3)])
        busy = [r for r in results if r == 429]
        started = [r for r in results if isinstance(r, dict)]
        assert len(busy) == 1 and len(started) == 2     # 恰好 1 个 429，cap 个过闸
        assert get_weight_train_manager().active_count() == 2   # 实际作业数 == cap（不超）
        async with sm() as s:
            assert len(await wms.list_models(s)) == 2   # 被拒方无任何 model/run 落库（守门零副作用）
        rest.gate.set()                                 # 放行作业跑完，避免泄漏到后续测试
        for d in started:
            await _await_terminal(d["jobId"])
    finally:
        settings.max_weight_train_jobs = prev_cap
        wtm._manager = None
        await dispose_engine()


def test_ttl_prune_uses_injected_clock_running_never_pruned():
    """H-B6：假时钟推进过 TTL → 终态作业被淘汰；running 作业（无 finished_mono）绝不淘汰。"""
    clock = {"t": 1000.0}
    mgr = WeightTrainManager(clock=lambda: clock["t"])
    done = _TrainJob("done1", _job_spec(), clock=lambda: clock["t"])
    done.status = "succeeded"
    done.finished_mono = clock["t"]
    running = _TrainJob("run1", _job_spec(), clock=lambda: clock["t"])   # 保持 running、finished_mono=None
    mgr._jobs["done1"] = done
    mgr._jobs["run1"] = running

    clock["t"] = 1000.0 + 601.0   # 过 600s TTL
    mgr._prune_finished()
    assert "done1" not in mgr._jobs   # 终态过 TTL 被淘汰
    assert "run1" in mgr._jobs        # running 永不淘汰


def test_count_cap_prunes_oldest_finished():
    """H-B6：终态作业数超 cap → 按完成时间淘汰最旧，保留最新 cap 个。"""
    mgr = WeightTrainManager(clock=lambda: 10_000.0)
    total = _FINISHED_JOBS_CAP + 5
    for i in range(total):
        job = _TrainJob(f"j{i}", _job_spec())
        job.status = "succeeded"
        job.finished_mono = 9_500.0 + i   # 全在 TTL 内（10000-9568<600）；i 越大越新
        mgr._jobs[f"j{i}"] = job
    mgr._prune_finished()
    assert len(mgr._jobs) == _FINISHED_JOBS_CAP
    for i in range(5):                       # 最旧 5 个（finished_mono 最小）被淘汰
        assert f"j{i}" not in mgr._jobs
    assert f"j{total - 1}" in mgr._jobs      # 最新的保留
