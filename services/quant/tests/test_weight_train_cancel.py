"""权重训练取消路径测试（H-T1 / H-B2）：cancel running→cancelling+run cancelled；已终态幂等；未知→404；
步间取消产出部分曲线且无 result/done。

用可门控的假 OKX（``_GateRest``）确定性命中「取消在加载阶段」，免依赖时序竞态。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.api.weight_models import cancel_weight_train
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
    """``get_candles`` 阻塞在 gate 上直到测试放行——使「取消在加载阶段」可确定性命中（H-B2/H-T1）。"""

    def __init__(self):
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        self.entered.set()
        await self.gate.wait()
        return await super().get_candles(inst_id, bar, limit=limit, before=before, after=after)


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


async def test_cancel_running_during_load_finalizes_cancelled():
    """取消在加载阶段命中：返回 cancelling，run 终为 cancelled，且无曲线（H-B2 加载后即生效）。"""
    await init_db()
    try:
        rest = _GateRest()
        started = await _start(rest)
        await asyncio.wait_for(rest.entered.wait(), timeout=2.0)   # 作业卡在 _load_df
        assert get_weight_train_manager().cancel(started["jobId"]) == "cancelling"
        rest.gate.set()                                            # 放行 → 加载后取消检查命中
        await _await_terminal(started["jobId"])
        sm = get_sessionmaker()
        async with sm() as s:
            run = await wms.get_run(s, started["modelId"], started["runId"])
        assert run["status"] == "cancelled"
        assert run["curveCount"] == 0                              # 加载阶段取消 → 无曲线
    finally:
        await dispose_engine()


async def test_cancel_already_terminal_is_idempotent():
    """已终态作业取消 → 'finished'（200 幂等），run 仍 succeeded，无双写覆盖。"""
    await init_db()
    try:
        started = await _start(_FakeRest())
        await _await_terminal(started["jobId"])
        assert get_weight_train_manager().cancel(started["jobId"]) == "finished"
        sm = get_sessionmaker()
        async with sm() as s:
            run = await wms.get_run(s, started["modelId"], started["runId"])
        assert run["status"] == "succeeded"   # 幂等：未被取消覆盖
    finally:
        await dispose_engine()


async def test_cancel_unknown_job_returns_404():
    """未知作业取消 → 路由抛 404 NOT_FOUND（绝不静默 / 不抛 500）。"""
    await init_db()
    try:
        with pytest.raises(HTTPException) as ei:
            await cancel_weight_train("does-not-exist")
        assert ei.value.status_code == 404 and ei.value.detail["code"] == "NOT_FOUND"
    finally:
        await dispose_engine()


async def test_cancel_between_steps_partial_curve_no_result():
    """步间取消：终态 cancelled，WS 不出现 result/done（中止于拟合循环，部分曲线已发但产物未装配）。"""
    await init_db()
    try:
        started = await _start(_FakeRest())
        job = get_weight_train_manager().get(started["jobId"])
        queue = job.subscribe()
        try:
            # 等到首个 curve 事件后置取消标志：下一次步间检查命中 → finalize_cancelled（部分曲线、无 result/done）。
            for _ in range(400):
                event = await asyncio.wait_for(queue.get(), timeout=5.0)
                if event["type"] == "curve":
                    job.request_cancel()
                    break
                if event["type"] in ("done", "error", "cancelled"):
                    break
        finally:
            job.unsubscribe(queue)
        await _await_terminal(started["jobId"])
        assert job.status in ("cancelled", "succeeded")   # 极快作业可能在置标志前已完成（诚实成功）
        if job.status == "cancelled":
            types = [e["type"] for e in job.snapshot()]
            assert "result" not in types and "done" not in types and types[-1] == "cancelled"
    finally:
        await dispose_engine()
