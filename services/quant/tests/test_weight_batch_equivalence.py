"""等价性硬门禁（weight-multi-model-training 放行条件 3）：批次候选(method M) 与独立 ``/weight-models/train``
(method M) 在同输入下 ``weights/normStats/metrics`` **字节级相等**——守「零第二训练器」不变量。

候选走与 ``start_training`` 同构的 ``JobSpec``（同一 ``_TrainJob``），故等价性是结构性的；本测试端到端跑两条路径
落库后逐字段比对（绝不因批次编排引入任何数值漂移）。
"""
from __future__ import annotations

import asyncio

import app.factors.weight_batch_manager as wbm
import app.factors.weight_train_manager as wtm
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.factors import service as factor_service
from app.factors import weight_batch_service as wbs
from app.factors import weight_model_service as wms
from app.db import repositories as repo
from app.okx.types import Candle
from app.schemas import WeightBatchStartReq, WeightTrainStartReq
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


def _train_req(method: str) -> WeightTrainStartReq:
    return WeightTrainStartReq(**{**_RANGE, "members": _MEMBERS, "method": method,
                                  "config": {"normWindow": 100}, "horizon": 6})


def _batch_req(method: str) -> WeightBatchStartReq:
    return WeightBatchStartReq(**{**_RANGE, "members": _MEMBERS, "config": {"normWindow": 100},
                                  "horizon": 6, "candidates": [{"method": method}]})


async def _await_train_terminal(job_id: str, tries: int = 600) -> None:
    job = wtm.get_weight_train_manager().get(job_id)
    for _ in range(tries):
        if job is not None and job.is_terminal:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("独立训练未在限定时间内到达终态")


async def _await_batch_terminal(batch_id: int, tries: int = 600) -> None:
    rt = wbm.get_weight_batch_manager().get(batch_id)
    for _ in range(tries):
        if rt is not None and rt.is_terminal:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("批次未在限定时间内到达终态")


async def _run_independent(method: str) -> dict:
    factor_service.clear_candle_cache()
    sm = get_sessionmaker()
    async with sm() as s:
        started = await wms.start_training(s, _FakeRest(), _train_req(method))
    await _await_train_terminal(started["jobId"])
    async with sm() as s:
        return await wms.get_run(s, started["modelId"], started["runId"])


async def _run_candidate(method: str) -> dict:
    factor_service.clear_candle_cache()
    sm = get_sessionmaker()
    async with sm() as s:
        res = await wbs.start_batch(s, _FakeRest(), _batch_req(method))
    await _await_batch_terminal(res["batchId"])
    async with sm() as s:
        runs = await repo.list_batch_runs(s, res["batchId"])
    from app.schemas import weight_train_run_out
    return weight_train_run_out(runs[0], with_curve=False)


async def _assert_equivalent(method: str) -> None:
    independent = await _run_independent(method)
    candidate = await _run_candidate(method)
    assert independent["status"] == "succeeded" and candidate["status"] == "succeeded"
    assert candidate["weights"] == independent["weights"]        # 权重字节级相等
    assert candidate["normStats"] == independent["normStats"]    # 归一化契约字节级相等
    assert candidate["metrics"] == independent["metrics"]        # 指标字节级相等


async def test_candidate_equivalent_to_independent_linear():
    await init_db()
    wtm._manager = None
    wbm._manager = None
    try:
        await _assert_equivalent("linear")
    finally:
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()


async def test_candidate_equivalent_to_independent_equal():
    await init_db()
    wtm._manager = None
    wbm._manager = None
    try:
        await _assert_equivalent("equal")
    finally:
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()


async def test_candidate_equivalent_to_independent_ic_weight():
    await init_db()
    wtm._manager = None
    wbm._manager = None
    try:
        await _assert_equivalent("ic_weight")
    finally:
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()
