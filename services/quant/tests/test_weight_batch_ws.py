"""批次 WS 测试：roster 快照 / 打标转发 / 晚订阅回放 / 终态关闭（抄 test_weight_train_ws 范式）。

**回归 R3**：子作业终态后转发协程退出、``_await_all_children`` 不挂起、批次如期 ``batchDone``（若转发协程在
子作业终态后不退，``q.get()`` 永阻塞 → 批次悬挂 → ``_await_batch_terminal`` 超时失败）。
"""
from __future__ import annotations

import asyncio
import json

import app.factors.weight_batch_manager as wbm
import app.factors.weight_train_manager as wtm
from app.api.weight_batches import ws_weight_batch
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.factors import service as factor_service
from app.factors import weight_batch_service as wbs
from app.okx.types import Candle
from app.schemas import WeightBatchStartReq
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


def _batch_req(methods) -> WeightBatchStartReq:
    return WeightBatchStartReq(**{**_RANGE, "members": _MEMBERS, "config": {"normWindow": 100},
                                  "horizon": 6, "candidates": [{"method": m} for m in methods]})


async def _start_batch(methods) -> dict:
    factor_service.clear_candle_cache()
    sm = get_sessionmaker()
    async with sm() as s:
        return await wbs.start_batch(s, _FakeRest(), _batch_req(methods))


async def _await_batch_terminal(batch_id: int, tries: int = 800) -> None:
    rt = wbm.get_weight_batch_manager().get(batch_id)
    for _ in range(tries):
        if rt is not None and rt.is_terminal:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("批次未在限定时间内到达终态")


async def test_ws_event_sequence_batch_to_done():
    """R3：2 快候选跑完 → 批次如期 batchDone（转发协程终态即退，不挂起）；WS 回放 batch→candidate→batchDone。"""
    await init_db()
    wtm._manager = None
    wbm._manager = None
    try:
        started = await _start_batch(["linear", "equal"])
        await asyncio.wait_for(_await_batch_terminal(started["batchId"]), timeout=20.0)   # 不得悬挂（R3）
        ws = _FakeWS()
        await asyncio.wait_for(ws_weight_batch(ws, started["batchId"]), timeout=5.0)
        types = [e["type"] for e in ws.sent]
        assert types[0] == "batch"
        assert "candidate" in types
        assert types[-1] == "batchDone" and ws.closed
        ranking = ws.sent[-1]["data"]["ranking"]
        assert len(ranking) == 2 and any(r.get("isBest") for r in ranking)
    finally:
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()


async def test_ws_unknown_batch_sends_error_and_closes():
    await init_db()
    wtm._manager = None
    wbm._manager = None
    try:
        ws = _FakeWS()
        await ws_weight_batch(ws, 424242)
        assert ws.sent and ws.sent[0]["type"] == "error"
        assert ws.sent[0]["data"]["code"] == "NOT_FOUND" and ws.closed
    finally:
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()


async def test_ws_candidate_events_are_labelled():
    """转发的候选事件带 runId/method 打标，且 run/result/done 内部事件被并入批次流。"""
    await init_db()
    wtm._manager = None
    wbm._manager = None
    try:
        started = await _start_batch(["linear", "equal"])
        await asyncio.wait_for(_await_batch_terminal(started["batchId"]), timeout=20.0)
        ws = _FakeWS()
        await asyncio.wait_for(ws_weight_batch(ws, started["batchId"]), timeout=5.0)
        candidate_events = [e for e in ws.sent if e["type"] == "candidate"]
        assert candidate_events
        assert all("runId" in e["data"] and "method" in e["data"] and "event" in e["data"]
                   for e in candidate_events)
        inner_types = {e["data"]["event"]["type"] for e in candidate_events}
        assert "run" in inner_types and "done" in inner_types   # 内部 run/done 被打标转发
    finally:
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()
