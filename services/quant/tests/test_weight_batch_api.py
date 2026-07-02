"""批次路由契约与错误码测试（weight-multi-model-training §四）：start/list/get/select/delete/候选取消。

直接驱动路由函数 + 假 WS/REST（仿 test_weight_train_cancel 范式），免起真实服务器；``get_rest_client``
monkeypatch 到假 OKX（注入给候选作业）。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import delete

import app.factors.weight_batch_manager as wbm
import app.factors.weight_train_manager as wtm
from app.api.weight_batches import (
    cancel_weight_batch_candidate,
    delete_weight_batch,
    get_weight_batch,
    list_weight_batches,
    select_weight_batch_run,
    start_weight_batch,
)
from app.config import get_settings
from app.db import repositories as repo
from app.db.models import WeightModel, WeightTrainBatch, WeightTrainRun
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.factors import service as factor_service
from app.okx.types import Candle
from app.schemas import WeightBatchSelectReq, WeightBatchStartReq
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


def _batch_req(methods, **extra) -> WeightBatchStartReq:
    return WeightBatchStartReq(**{**_RANGE, "members": _MEMBERS, "config": {"normWindow": 100},
                                  "horizon": 6, "candidates": [{"method": m} for m in methods], **extra})


async def _reset() -> None:
    await init_db()
    wtm._manager = None
    wbm._manager = None
    factor_service.clear_candle_cache()
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(delete(WeightTrainRun))
        await s.execute(delete(WeightTrainBatch))
        await s.execute(delete(WeightModel))
        await s.commit()


async def _await_batch_terminal(batch_id: int, tries: int = 800) -> None:
    rt = wbm.get_weight_batch_manager().get(batch_id)
    for _ in range(tries):
        if rt is not None and rt.is_terminal:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("批次未在限定时间内到达终态")


async def test_start_list_get_contract(monkeypatch):
    monkeypatch.setattr("app.api.weight_batches.get_rest_client", lambda: _FakeRest())
    await _reset()
    sm = get_sessionmaker()
    try:
        async with sm() as s:
            res = await start_weight_batch(_batch_req(["linear", "equal"]), session=s)
        assert res["batchId"] and res["parentModelId"] and len(res["candidates"]) == 2
        assert all(c["status"] == "queued" for c in res["candidates"])
        bid = res["batchId"]
        async with sm() as s:
            listing = await list_weight_batches(session=s)
        assert any(b["batchId"] == bid and b["candidateCount"] == 2 for b in listing)
        await _await_batch_terminal(bid)
        async with sm() as s:
            detail = await get_weight_batch(bid, session=s)
        assert detail["status"] == "succeeded" and len(detail["candidates"]) == 2
        assert any(r.get("isBest") for r in detail["ranking"])   # 最优候选标记
    finally:
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()


async def test_get_unknown_batch_404():
    await _reset()
    sm = get_sessionmaker()
    try:
        with pytest.raises(HTTPException) as ei:
            async with sm() as s:
                await get_weight_batch(999999, session=s)
        assert ei.value.status_code == 404
    finally:
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()


async def test_select_succeeded_and_not_selectable(monkeypatch):
    monkeypatch.setattr("app.api.weight_batches.get_rest_client", lambda: _FakeRest())
    await _reset()
    sm = get_sessionmaker()
    try:
        # 成功批次：择优一条 succeeded → 200 写 selectedRunId；不存在候选 → 404。
        async with sm() as s:
            ok = await start_weight_batch(_batch_req(["linear", "equal"]), session=s)
        await _await_batch_terminal(ok["batchId"])
        async with sm() as s:
            detail = await get_weight_batch(ok["batchId"], session=s)
        succeeded = [c["id"] for c in detail["candidates"] if c["status"] == "succeeded"]
        async with sm() as s:
            picked = await select_weight_batch_run(ok["batchId"], WeightBatchSelectReq(runId=succeeded[0]), session=s)
        assert picked["selectedRunId"] == succeeded[0]
        with pytest.raises(HTTPException) as ei404:
            async with sm() as s:
                await select_weight_batch_run(ok["batchId"], WeightBatchSelectReq(runId=987654), session=s)
        assert ei404.value.status_code == 404

        # 失败候选：normWindow 过大 → 样本不足 → failed；择优 failed → 409 WEIGHT_BATCH_RUN_NOT_SELECTABLE。
        async with sm() as s:
            bad = await start_weight_batch(
                _batch_req(["linear"], config={"normWindow": 400}), session=s)
        await _await_batch_terminal(bad["batchId"])
        async with sm() as s:
            bad_detail = await get_weight_batch(bad["batchId"], session=s)
        failed_id = bad_detail["candidates"][0]["id"]
        assert bad_detail["candidates"][0]["status"] == "failed"
        with pytest.raises(HTTPException) as ei409:
            async with sm() as s:
                await select_weight_batch_run(bad["batchId"], WeightBatchSelectReq(runId=failed_id), session=s)
        assert ei409.value.status_code == 409
        assert ei409.value.detail["code"] == "WEIGHT_BATCH_RUN_NOT_SELECTABLE"
    finally:
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()


async def test_delete_running_409_then_cancel_and_delete_204(monkeypatch):
    from app.factors import weight_batch_service as wbs
    prev = get_settings().max_weight_train_jobs
    get_settings().max_weight_train_jobs = 2
    await _reset()
    sm = get_sessionmaker()
    gate_rest = _GateRest()   # 卡住候选于 running，先验 409，再取消放行后删 204。
    monkeypatch.setattr("app.api.weight_batches.get_rest_client", lambda: gate_rest)
    try:
        async with sm() as s:
            res = await start_weight_batch(_batch_req(["linear", "equal"]), session=s)
        bid = res["batchId"]
        for _ in range(400):                       # 等到候选进入 running（阻塞 gate）
            async with sm() as s:
                runs = await repo.list_batch_runs(s, bid)
            if any(r.status == "running" for r in runs):
                break
            await asyncio.sleep(0.02)
        with pytest.raises(HTTPException) as ei:    # 仍有 running/queued → 409
            async with sm() as s:
                await delete_weight_batch(bid, session=s)
        assert ei.value.status_code == 409 and ei.value.detail["code"] == "WEIGHT_BATCH_RUNNING"
        # 取消 → 放行 → 达终态 → 删除 204，父模型 + 候选 + 批次行俱删。
        async with sm() as s:
            await wbs.cancel_batch(s, bid)
        gate_rest.gate.set()
        await _await_batch_terminal(bid)
        async with sm() as s:
            resp = await delete_weight_batch(bid, session=s)
        assert resp.status_code == 204
        async with sm() as s:
            assert await repo.get_weight_batch(s, bid) is None
            assert await repo.get_weight_model(s, res["parentModelId"]) is None
            assert (await repo.list_batch_runs(s, bid)) == []
    finally:
        get_settings().max_weight_train_jobs = prev
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()


async def test_too_many_candidates_400(monkeypatch):
    monkeypatch.setattr("app.api.weight_batches.get_rest_client", lambda: _FakeRest())
    prev = get_settings().max_weight_batch_candidates
    get_settings().max_weight_batch_candidates = 2
    await _reset()
    sm = get_sessionmaker()
    try:
        with pytest.raises(HTTPException) as ei:
            async with sm() as s:
                await start_weight_batch(_batch_req(["linear", "equal", "ic_weight"]), session=s)
        assert ei.value.status_code == 400
        assert ei.value.detail["code"] == "WEIGHT_BATCH_TOO_MANY_CANDIDATES"
    finally:
        get_settings().max_weight_batch_candidates = prev
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()


async def test_candidate_cancel_unknown_batch_404():
    await _reset()
    sm = get_sessionmaker()
    try:
        with pytest.raises(HTTPException) as ei:
            async with sm() as s:
                await cancel_weight_batch_candidate(999999, 1, session=s)
        assert ei.value.status_code == 404
    finally:
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()
