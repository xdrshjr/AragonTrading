"""权重训练 WS 测试：事件序列、晚订阅 / 快作业回放（R-06）、断线仍落库（历史持久）。

直接驱动路由 ``ws_weight_train`` + 假 WS（仿 test_agent_ws 范式），免起真实服务器。
"""
from __future__ import annotations

import asyncio
import json

from fastapi import WebSocketDisconnect

from app.api.weight_models import ws_weight_train
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.factors import service as factor_service
from app.factors import weight_model_service as wms
from app.factors.weight_train_manager import get_weight_train_manager
from app.okx.types import Candle
from app.schemas import WeightTrainStartReq
from tests.conftest import make_synthetic_candles

_TERMINAL = ("done", "error", "cancelled")
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


class _DisconnectWS(_FakeWS):
    """读到第 ``after`` 条即模拟断线（断 WS 不取消作业，续跑落库，R-06/解耦）。"""

    def __init__(self, after: int):
        super().__init__()
        self._after = after

    async def send_text(self, text):
        await super().send_text(text)
        if len(self.sent) >= self._after:
            raise WebSocketDisconnect()


def _req(**extra) -> WeightTrainStartReq:
    return WeightTrainStartReq(**{**_RANGE, "members": _MEMBERS, "method": "linear",
                                  "config": {"normWindow": 100}, "horizon": 6, **extra})


async def _start_job() -> dict:
    factor_service.clear_candle_cache()
    sm = get_sessionmaker()
    async with sm() as s:
        return await wms.start_training(s, _FakeRest(), _req())


async def _await_terminal(job_id: str, tries: int = 400) -> None:
    job = get_weight_train_manager().get(job_id)
    for _ in range(tries):
        if job is not None and job.is_terminal:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("作业未在限定时间内到达终态")


async def test_ws_event_sequence_run_to_done():
    await init_db()
    try:
        started = await _start_job()
        ws = _FakeWS()
        await ws_weight_train(ws, started["jobId"])   # 跑到 done 才返回
        types = [e["type"] for e in ws.sent]
        assert types[0] == "run"
        assert "phase" in types and "curve" in types and "result" in types
        assert types[-1] == "done" and ws.closed
        # 末步 result 与持久化一致：run 落库 succeeded。
        sm = get_sessionmaker()
        async with sm() as s:
            run = await wms.get_run(s, started["modelId"], started["runId"])
        assert run["status"] == "succeeded"
    finally:
        await dispose_engine()


async def test_ws_unknown_job_sends_error_and_closes():
    await init_db()
    try:
        ws = _FakeWS()
        await ws_weight_train(ws, "nonexistent-job")
        assert ws.sent and ws.sent[0]["type"] == "error"
        assert ws.sent[0]["data"]["code"] == "NOT_FOUND" and ws.closed
    finally:
        await dispose_engine()


async def test_ws_late_subscribe_replays_terminal_no_hang():
    """R-06：作业已终态后才订阅 → 收完整回放（run…done）后立即关闭，绝不悬挂。"""
    await init_db()
    try:
        started = await _start_job()
        await _await_terminal(started["jobId"])   # 先等作业跑完
        ws = _FakeWS()
        await asyncio.wait_for(ws_weight_train(ws, started["jobId"]), timeout=5.0)  # 不得悬挂
        types = [e["type"] for e in ws.sent]
        assert types[0] == "run" and types[-1] == "done" and ws.closed
    finally:
        await dispose_engine()


async def test_ws_disconnect_still_persists_history():
    """断线（订阅中途）后作业继续跑完并落库 succeeded（作业与 socket 解耦，历史持久）。"""
    await init_db()
    try:
        started = await _start_job()
        ws = _DisconnectWS(after=1)   # 收到首条即断
        await ws_weight_train(ws, started["jobId"])   # 捕获 WebSocketDisconnect 后返回
        await _await_terminal(started["jobId"])       # 作业不受断线影响，继续跑完
        sm = get_sessionmaker()
        async with sm() as s:
            run = await wms.get_run(s, started["modelId"], started["runId"])
        assert run["status"] == "succeeded" and run["weights"]
        # 断线后重连可回放完整历史（含终态）。
        ws2 = _FakeWS()
        await asyncio.wait_for(ws_weight_train(ws2, started["jobId"]), timeout=5.0)
        assert [e["type"] for e in ws2.sent][-1] == "done"
    finally:
        await dispose_engine()
