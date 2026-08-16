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
        self.headers: dict = {}   # 无 Origin ⇒ 非浏览器客户端，来源守卫放行（app/api/ws_guard.py）
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


class _PingThenDisconnectWS(_FakeWS):
    """收到首个 ping 心跳即模拟断线，使 ``ws_weight_train`` 返回（H-B7 心跳测试免无限阻塞）。"""

    async def send_text(self, text):
        await super().send_text(text)
        if json.loads(text).get("type") == "ping":
            raise WebSocketDisconnect()


class _GateRest(_FakeRest):
    """``get_candles`` 阻塞在 gate 上直到放行——使作业可停在加载阶段（仅 run/phase 缓冲），供 H-B7/取消测试。"""

    def __init__(self):
        super().__init__()
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        self.entered.set()
        await self.gate.wait()
        return await super().get_candles(inst_id, bar, limit=limit, before=before, after=after)


async def _start_with_rest(rest) -> dict:
    factor_service.clear_candle_cache()
    sm = get_sessionmaker()
    async with sm() as s:
        return await wms.start_training(s, rest, _req())


async def _start_failing_job() -> dict:
    """启动一个必然失败的作业（normWindow > 可用根数 → 样本不足 → error 终态）。"""
    factor_service.clear_candle_cache()
    sm = get_sessionmaker()
    async with sm() as s:
        return await wms.start_training(s, _FakeRest(), _req(config={"normWindow": 400}))


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


async def test_ws_late_subscribe_replays_error_terminal():
    """H-T4：失败作业终态后才订阅 → 完整回放至 error 终态并关闭，绝不悬挂。"""
    await init_db()
    try:
        started = await _start_failing_job()
        await _await_terminal(started["jobId"])
        ws = _FakeWS()
        await asyncio.wait_for(ws_weight_train(ws, started["jobId"]), timeout=5.0)
        types = [e["type"] for e in ws.sent]
        assert types[0] == "run" and types[-1] == "error" and ws.closed
        assert ws.sent[-1]["data"]["code"] == "WEIGHT_TRAIN_INSUFFICIENT_SAMPLES"
    finally:
        await dispose_engine()


async def test_ws_late_subscribe_replays_cancelled_terminal():
    """H-T4：取消作业终态后才订阅 → 完整回放至 cancelled 终态并关闭，绝不悬挂。"""
    await init_db()
    try:
        rest = _GateRest()
        started = await _start_with_rest(rest)
        await asyncio.wait_for(rest.entered.wait(), timeout=2.0)
        get_weight_train_manager().cancel(started["jobId"])   # 加载阶段置取消标志
        rest.gate.set()                                       # 放行 → 加载后取消检查命中 → cancelled
        await _await_terminal(started["jobId"])
        ws = _FakeWS()
        await asyncio.wait_for(ws_weight_train(ws, started["jobId"]), timeout=5.0)
        types = [e["type"] for e in ws.sent]
        assert types[-1] == "cancelled" and ws.closed
    finally:
        await dispose_engine()


async def test_ws_terminal_fallback_replays_dropped_result(monkeypatch):
    """G-B3/G-T3：满队列丢 result（权重载荷）后，终态兜底路径在关闭前补发最后一条 result + 终态 done，
    客户端仍拿到权重（``setResult`` 幂等覆盖，重投安全）。"""
    from app.factors.service import FactorWindow
    from app.factors.weight_train_manager import JobSpec, _TrainJob, get_weight_train_manager
    await init_db()
    try:
        monkeypatch.setattr("app.api.weight_models._WS_HEARTBEAT_SECONDS", 0.05)
        spec = JobSpec(run_id=1, model_id=1, rest=None, members=[], method="linear", config={},
                       window=FactorWindow(inst_id="BTC-USDT-SWAP", bar="1H", start_ts=1, end_ts=2,
                                           max_bars=None), horizon=6, run_cap=50, curve_steps=6)
        job = _TrainJob("gb3-job", spec)
        job._push({"type": "run", "data": {"runId": 1}})        # 非终态前缀缓冲（snapshot 不含终态）
        get_weight_train_manager()._jobs["gb3-job"] = job

        ws = _FakeWS()
        task = asyncio.create_task(ws_weight_train(ws, "gb3-job"))
        await asyncio.sleep(0.02)                                # WS 已发 snapshot、进入等待循环
        sub_q = next(iter(job._subscribers))
        while not sub_q.full():                                  # 填满该订阅者队列（无 await，原子）
            sub_q.put_nowait({"type": "filler"})
        job._push({"type": "result", "data": {                  # 队列满 → result/done 被 _push 丢弃
            "method": "linear", "weights": {"mom_roc": 1.0}, "normStats": {}, "metrics": {},
            "trainerVersion": None, "note": "", "provenance": {}}})
        job._push({"type": "done", "data": {"runId": 1}})
        job.status = "succeeded"                                 # 转终态 → 触发兜底分支
        await asyncio.wait_for(task, timeout=5.0)
        types = [e["type"] for e in ws.sent]
        assert "result" in types and types[-1] == "done" and ws.closed
        replayed = [e for e in ws.sent if e["type"] == "result"][-1]
        assert replayed["data"]["weights"] == {"mom_roc": 1.0}  # 补发的 result 含权重载荷
    finally:
        get_weight_train_manager()._jobs.pop("gb3-job", None)
        await dispose_engine()


async def test_ws_heartbeat_ping_on_silence(monkeypatch):
    """H-B7：流式静默超 _WS_HEARTBEAT_SECONDS → 下发 ping 心跳（半开探测）。"""
    await init_db()
    try:
        monkeypatch.setattr("app.api.weight_models._WS_HEARTBEAT_SECONDS", 0.05)
        rest = _GateRest()
        started = await _start_with_rest(rest)
        await asyncio.wait_for(rest.entered.wait(), timeout=2.0)   # 作业卡在 _load_df，仅 run/phase 缓冲
        ws = _PingThenDisconnectWS()
        await asyncio.wait_for(ws_weight_train(ws, started["jobId"]), timeout=5.0)
        assert any(e["type"] == "ping" for e in ws.sent)
        rest.gate.set()                                           # 放行作业跑完，清理
        await _await_terminal(started["jobId"])
    finally:
        await dispose_engine()
