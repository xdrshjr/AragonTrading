"""批次取消测试（weight-multi-model-training §2.3）：整批取消 running→cancelled、queued→cancelled、
无 DB 泄漏、批次派生 status=cancelled；单候选取消（running / queued）。

用可门控假 OKX（``_GateRest``）确定性命中「取消在加载阶段」，免依赖时序竞态。
"""
from __future__ import annotations

import asyncio

from sqlalchemy import delete

import app.factors.weight_batch_manager as wbm
import app.factors.weight_train_manager as wtm
from app.config import get_settings
from app.db import repositories as repo
from app.db.models import WeightModel, WeightTrainBatch, WeightTrainRun
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.factors import service as factor_service
from app.factors import weight_batch_service as wbs
from app.okx.types import Candle
from app.schemas import weight_batch_status, WeightBatchStartReq
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
    def __init__(self):
        self.gate = asyncio.Event()

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        await self.gate.wait()
        return await super().get_candles(inst_id, bar, limit=limit, before=before, after=after)


def _batch_req(methods) -> WeightBatchStartReq:
    return WeightBatchStartReq(**{**_RANGE, "members": _MEMBERS, "config": {"normWindow": 100},
                                  "horizon": 6, "candidates": [{"method": m} for m in methods]})


async def _reset(cap: int) -> None:
    await init_db()
    get_settings().max_weight_train_jobs = cap
    wtm._manager = None
    wbm._manager = None
    factor_service.clear_candle_cache()
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(delete(WeightTrainRun))
        await s.execute(delete(WeightTrainBatch))
        await s.execute(delete(WeightModel))
        await s.commit()


async def _batch_runs(batch_id: int) -> list:
    sm = get_sessionmaker()
    async with sm() as s:
        return await repo.list_batch_runs(s, batch_id)


async def _wait_running_count(batch_id: int, n: int) -> None:
    for _ in range(400):
        runs = await _batch_runs(batch_id)
        if sum(1 for r in runs if r.status == "running") >= n:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("候选未在限定时间内进入 running")


async def _await_batch_terminal(batch_id: int, tries: int = 800) -> None:
    rt = wbm.get_weight_batch_manager().get(batch_id)
    for _ in range(tries):
        if rt is not None and rt.is_terminal:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("批次未在限定时间内到达终态")


async def test_cancel_whole_batch_running_and_queued_to_cancelled():
    """整批取消：2 running（gated）+ 2 queued → 全 cancelled，无 DB running/queued 泄漏，派生 status=cancelled。"""
    prev = get_settings().max_weight_train_jobs
    await _reset(2)
    rest = _GateRest()
    sm = get_sessionmaker()
    try:
        async with sm() as s:
            res = await wbs.start_batch(s, rest, _batch_req(["linear", "equal", "ic_weight", "linear"]))
        bid = res["batchId"]
        await _wait_running_count(bid, 2)
        assert sum(1 for r in await _batch_runs(bid) if r.status == "queued") == 2
        async with sm() as s:
            assert await wbs.cancel_batch(s, bid) == "cancelling"
        rest.gate.set()                                  # 放行 running 候选 → 加载后取消检查命中 → cancelled
        await _await_batch_terminal(bid)
        runs = await _batch_runs(bid)
        assert all(r.status == "cancelled" for r in runs)          # 全 cancelled
        assert not any(r.status in ("running", "queued") for r in runs)   # 无 DB 泄漏
        assert weight_batch_status(runs) == "cancelled"
    finally:
        get_settings().max_weight_train_jobs = prev
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()


async def test_cancel_single_queued_candidate():
    """单候选取消（queued）：仅目标候选 cancelled，其余不受影响。"""
    prev = get_settings().max_weight_train_jobs
    await _reset(2)
    rest = _GateRest()
    sm = get_sessionmaker()
    try:
        async with sm() as s:
            res = await wbs.start_batch(s, rest, _batch_req(["linear", "equal", "ic_weight", "linear"]))
        bid = res["batchId"]
        await _wait_running_count(bid, 2)
        queued_run_id = res["candidates"][2]["runId"]   # 第 3 个候选（queued）
        async with sm() as s:
            assert await wbs.cancel_candidate(s, bid, queued_run_id) == "cancelling"
        runs = {r.id: r.status for r in await _batch_runs(bid)}
        assert runs[queued_run_id] == "cancelled"
        # 清理：整批取消 + 放行 + 等终态。
        async with sm() as s:
            await wbs.cancel_batch(s, bid)
        rest.gate.set()
        await _await_batch_terminal(bid)
    finally:
        get_settings().max_weight_train_jobs = prev
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()


async def test_cancel_single_running_candidate():
    """单候选取消（running）：返回 cancelling，放行后该候选 cancelled。"""
    prev = get_settings().max_weight_train_jobs
    await _reset(2)
    rest = _GateRest()
    sm = get_sessionmaker()
    try:
        async with sm() as s:
            res = await wbs.start_batch(s, rest, _batch_req(["linear", "equal"]))
        bid = res["batchId"]
        await _wait_running_count(bid, 2)
        running_run_id = res["candidates"][0]["runId"]
        async with sm() as s:
            assert await wbs.cancel_candidate(s, bid, running_run_id) == "cancelling"
        rest.gate.set()
        await _await_batch_terminal(bid)
        runs = {r.id: r.status for r in await _batch_runs(bid)}
        assert runs[running_run_id] == "cancelled"
    finally:
        get_settings().max_weight_train_jobs = prev
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()
