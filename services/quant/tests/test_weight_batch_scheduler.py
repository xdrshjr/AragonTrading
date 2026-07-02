"""批次调度泵测试（weight-multi-model-training 放行条件 2）：并发上限、FIFO 队列、名额释放泵起。

**含 R2 丢唤醒回归**：快桩作业即时终态 + K>cap → 排队候选绝不卡 ``queued``、全达终态、任一时刻
``active_count ≤ cap``（若泵存在两段式 reserve↔wait 丢唤醒，排队候选会永久卡死，本测试即失败）。
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
from app.schemas import WeightBatchStartReq
from tests.conftest import make_synthetic_candles

_RANGE = {"instId": "BTC-USDT-SWAP", "bar": "1H",
          "startTs": 1_700_000_000, "endTs": 1_700_000_000 + 295 * 3600}
_MEMBERS = [
    {"factorKey": "mom_roc", "params": {"win": 20}, "direction": "long"},
    {"factorKey": "trend_ema_dist", "params": {}, "direction": "long"},
    {"factorKey": "rev_rsi", "params": {}, "direction": "short"},
]
_METHODS6 = ["linear", "equal", "ic_weight", "linear", "equal", "ic_weight"]


class _FakeRest:
    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        rows = make_synthetic_candles(min(limit, 300), start_ts=1_700_000_000, step=3600)
        return [Candle(ts=r["ts"], o=r["o"], h=r["h"], l=r["l"], c=r["c"], vol=r["vol"]) for r in rows]


class _GateRest(_FakeRest):
    """``get_candles`` 阻塞在 gate 上 → 已起候选占住 running 名额，使并发上限可被确定性检验。"""

    def __init__(self):
        self.gate = asyncio.Event()

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        await self.gate.wait()
        return await super().get_candles(inst_id, bar, limit=limit, before=before, after=after)


def _batch_req(methods, **extra) -> WeightBatchStartReq:
    return WeightBatchStartReq(**{**_RANGE, "members": _MEMBERS, "config": {"normWindow": 100},
                                  "horizon": 6, "candidates": [{"method": m} for m in methods], **extra})


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


async def _start_batch(rest, methods) -> dict:
    sm = get_sessionmaker()
    async with sm() as s:
        return await wbs.start_batch(s, rest, _batch_req(methods))


async def _count_status(batch_id: int, status: str) -> int:
    sm = get_sessionmaker()
    async with sm() as s:
        runs = await repo.list_batch_runs(s, batch_id)
    return sum(1 for r in runs if r.status == status)


async def _await_batch_terminal(batch_id: int, tries: int = 1500) -> None:
    rt = wbm.get_weight_batch_manager().get(batch_id)
    for _ in range(tries):
        if rt is not None and rt.is_terminal:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("批次未在限定时间内到达终态")


async def test_cap_respected_and_queue_pumps_after_slot_free():
    """cap=2、单批 6 候选（gated）：恰好 2 running + 4 queued（cap 不击穿）；放行后队列被逐一泵起，全 succeeded。"""
    prev = get_settings().max_weight_train_jobs
    await _reset(2)
    rest = _GateRest()
    try:
        res = await _start_batch(rest, _METHODS6)
        bid = res["batchId"]
        # 等到 2 候选占满 running 名额。以 in-mem ``active_count`` 为同步点（而非 DB status 计数）：
        # 泵先 ``mark_run_running``（DB→running）再 ``start_job``（登记 in-mem 计入 active_count），两步间
        # DB 计数会瞬时领先 active_count；等 active_count 自身到 cap 才是稳定态（cap 封顶，>=2 即恰 ==2）。
        for _ in range(400):
            if wtm.get_weight_train_manager().active_count() >= 2:
                break
            await asyncio.sleep(0.02)
        assert wtm.get_weight_train_manager().active_count() == 2   # cap 不击穿（名额恰满）
        assert await _count_status(bid, "running") == 2
        assert await _count_status(bid, "queued") == 4             # 超额候选进 FIFO 队列
        rest.gate.set()                                            # 放行 → 名额释放即泵起后续
        await _await_batch_terminal(bid)
        assert await _count_status(bid, "succeeded") == 6         # 全部候选被泵起并成功
        assert await _count_status(bid, "queued") == 0            # 无候选卡 queued
    finally:
        get_settings().max_weight_train_jobs = prev
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()


async def test_fast_jobs_no_stuck_queued_lost_wakeup_regression():
    """R2 回归：cap=2、6 快候选（无 gate、即时终态）→ 全达终态、无候选卡 queued、任一时刻 active_count ≤ cap。

    若调度泵是「锁外 reserve 失败 → 再 await wait」的两段式，快作业终态的 notify 会丢唤醒，排队候选永久卡
    ``queued`` → 本测试超时失败。检查-等待同锁闭环（reserve_when_free）杜绝之。
    """
    prev = get_settings().max_weight_train_jobs
    await _reset(2)
    observed_max = 0
    try:
        res = await _start_batch(_FakeRest(), _METHODS6)
        bid = res["batchId"]
        rt = wbm.get_weight_batch_manager().get(bid)

        async def _monitor():
            nonlocal observed_max
            while rt is not None and not rt.is_terminal:
                observed_max = max(observed_max, wtm.get_weight_train_manager().active_count())
                await asyncio.sleep(0.003)

        mon = asyncio.create_task(_monitor())
        await _await_batch_terminal(bid)
        await mon
        assert observed_max <= 2                                  # 任一采样时刻并发不超 cap
        assert await _count_status(bid, "succeeded") == 6         # 全达终态成功（无丢唤醒卡死）
        assert await _count_status(bid, "queued") == 0
        # batchDone 已 emit，含 6 条 ranking
        done = [e for e in rt.snapshot() if e["type"] == "batchDone"]
        assert done and len(done[-1]["data"]["ranking"]) == 6
    finally:
        get_settings().max_weight_train_jobs = prev
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()


async def test_mark_running_db_failure_releases_slot_and_fails_candidate(monkeypatch):
    """P1 回归：候选起跑前 ``mark_run_running`` 落库异常（如 SQLite database is locked）→ 释放裸预占名额
    （**无泄漏**，否则全局有效 cap 被永久削）、该候选就地判 failed、其余候选照常成功、批次如期 batchDone
    （**非过早**收口）。若 reserve↔start 窗口无 try/except，则名额泄漏 + 泵中断 + 过早 batchDone → 本测试失败。"""
    prev = get_settings().max_weight_train_jobs
    await _reset(2)
    original = repo.mark_run_running
    calls = {"n": 0}

    async def _flaky_mark_running(session, run_id, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:                                # 仅首个（FIFO）候选落库失败，其余正常
            raise RuntimeError("database is locked")
        return await original(session, run_id, *args, **kwargs)

    monkeypatch.setattr(repo, "mark_run_running", _flaky_mark_running)
    try:
        res = await _start_batch(_FakeRest(), _METHODS6)
        bid = res["batchId"]
        await _await_batch_terminal(bid)
        mgr = wtm.get_weight_train_manager()
        assert mgr.active_count() == 0                      # 无残留 running 作业
        assert mgr._reserved == 0                           # 关键：裸预占已释放，杜绝名额泄漏
        assert await _count_status(bid, "failed") == 1      # 落库失败的候选就地判 failed
        assert await _count_status(bid, "succeeded") == 5   # 其余候选被泵起并成功
        assert await _count_status(bid, "queued") == 0      # 无候选悬挂 queued
        rt = wbm.get_weight_batch_manager().get(bid)
        done = [e for e in rt.snapshot() if e["type"] == "batchDone"]
        assert done and len(done[-1]["data"]["ranking"]) == 6   # batchDone 定稿含全部 6 候选（非过早收口）
    finally:
        get_settings().max_weight_train_jobs = prev
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()
