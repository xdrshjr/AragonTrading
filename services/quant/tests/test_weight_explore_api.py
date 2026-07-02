"""因子组合探索路由契约与错误码测试（factor-combo-exploration §6.1）。

直接驱动路由 / service + 假 OKX（仿 test_weight_batch_api / test_weight_batch_equivalence 范式），免起真实
服务器。覆盖：守门序（全部 400 先于 413 区间守门）、成功启动（kind=explore + 父模型 source=batch + 逐候选
members_json = 对应组合 + explore_plan_json 落库）、复用批次端点（GET/select/DELETE，select 仅指针不改父模型
成员）、等价性硬门禁（探索候选子集 ≡ 独立 train）、按候选回测（用 run.members_json）。
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import delete

import app.factors.weight_batch_manager as wbm
import app.factors.weight_train_manager as wtm
from app.api.weight_batches import (
    delete_weight_batch,
    get_weight_batch,
    select_weight_batch_run,
    start_weight_exploration,
)
from app.config import get_settings
from app.db import repositories as repo
from app.db.models import WeightModel, WeightTrainBatch, WeightTrainRun
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.factors import service as factor_service
from app.factors import weight_model_service as wms
from app.okx.types import Candle
from app.schemas import WeightBatchSelectReq, WeightExploreStartReq, WeightTrainStartReq
from tests.conftest import make_synthetic_candles

_RANGE = {"instId": "BTC-USDT-SWAP", "bar": "1H",
          "startTs": 1_700_000_000, "endTs": 1_700_000_000 + 295 * 3600}
_HUGE_RANGE = {"instId": "BTC-USDT-SWAP", "bar": "1H",
               "startTs": 1_700_000_000, "endTs": 1_700_000_000 + 9000 * 3600}
_MEMBERS = [
    {"factorKey": "mom_roc", "params": {"win": 20}, "direction": "long"},
    {"factorKey": "trend_ema_dist", "params": {}, "direction": "long"},
    {"factorKey": "rev_rsi", "params": {}, "direction": "short"},
]


class _FakeRest:
    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        rows = make_synthetic_candles(min(limit, 300), start_ts=1_700_000_000, step=3600)
        return [Candle(ts=r["ts"], o=r["o"], h=r["h"], l=r["l"], c=r["c"], vol=r["vol"]) for r in rows]


def _explore_req(*, strategy="manual", params=None, methods=("linear",),
                 pool=None, range_over=None, **extra) -> WeightExploreStartReq:
    base = range_over or _RANGE
    return WeightExploreStartReq(**{**base, "pool": pool or _MEMBERS, "strategy": strategy,
                                    "params": params or {}, "methods": list(methods),
                                    "config": {"normWindow": 100}, "horizon": 6, **extra})


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
    raise AssertionError("探索批次未在限定时间内到达终态")


# ============================ 守门序（全部 400 先于 413） ============================
async def test_invalid_plan_400(monkeypatch):
    monkeypatch.setattr("app.api.weight_batches.get_rest_client", lambda: _FakeRest())
    await _reset()
    sm = get_sessionmaker()
    try:
        with pytest.raises(HTTPException) as ei:
            async with sm() as s:
                await start_weight_exploration(
                    _explore_req(strategy="manual", params={"combos": [[0, 9]]}), session=s)
        assert ei.value.status_code == 400
        assert ei.value.detail["code"] == "WEIGHT_EXPLORE_INVALID_PLAN"
    finally:
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()


async def test_too_few_candidates_400(monkeypatch):
    monkeypatch.setattr("app.api.weight_batches.get_rest_client", lambda: _FakeRest())
    await _reset()
    sm = get_sessionmaker()
    try:
        with pytest.raises(HTTPException) as ei:
            async with sm() as s:  # 1 组合 × 1 方法 = 1 候选 < 2
                await start_weight_exploration(
                    _explore_req(strategy="manual", params={"combos": [[0, 1]]}), session=s)
        assert ei.value.status_code == 400
        assert ei.value.detail["code"] == "WEIGHT_EXPLORE_TOO_FEW_CANDIDATES"
    finally:
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()


async def test_too_many_candidates_400(monkeypatch):
    monkeypatch.setattr("app.api.weight_batches.get_rest_client", lambda: _FakeRest())
    prev = get_settings().max_weight_explore_candidates
    get_settings().max_weight_explore_candidates = 2
    await _reset()
    sm = get_sessionmaker()
    try:
        with pytest.raises(HTTPException) as ei:  # sizes=[2] → C(3,2)=3 组合 × 1 方法 = 3 > cap(2)
            async with sm() as s:
                await start_weight_exploration(
                    _explore_req(strategy="sizes", params={"sizes": [2]}), session=s)
        assert ei.value.status_code == 400
        assert ei.value.detail["code"] == "WEIGHT_EXPLORE_TOO_MANY_CANDIDATES"
    finally:
        get_settings().max_weight_explore_candidates = prev
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()


async def test_range_too_long_413(monkeypatch):
    monkeypatch.setattr("app.api.weight_batches.get_rest_client", lambda: _FakeRest())
    await _reset()
    sm = get_sessionmaker()
    try:
        with pytest.raises(HTTPException) as ei:  # 2 候选（过 400），但区间过长 → 413
            async with sm() as s:
                await start_weight_exploration(
                    _explore_req(strategy="manual", params={"combos": [[0, 1], [0, 2]]},
                                 range_over=_HUGE_RANGE), session=s)
        assert ei.value.status_code == 413
        assert ei.value.detail["code"] == "FACTOR_TRAIN_TOO_LARGE"
    finally:
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()


async def test_too_many_candidates_400_precedes_range_413(monkeypatch):
    """候选数 400 一律先于区间 413（P1-2）：同时越两界时须报 400 TOO_MANY，绝不先 413。"""
    monkeypatch.setattr("app.api.weight_batches.get_rest_client", lambda: _FakeRest())
    prev = get_settings().max_weight_explore_candidates
    get_settings().max_weight_explore_candidates = 2
    await _reset()
    sm = get_sessionmaker()
    try:
        with pytest.raises(HTTPException) as ei:  # 3 候选 > cap(2) 且区间过长 → 应 400（先于 413）
            async with sm() as s:
                await start_weight_exploration(
                    _explore_req(strategy="sizes", params={"sizes": [2]}, range_over=_HUGE_RANGE),
                    session=s)
        assert ei.value.status_code == 400
        assert ei.value.detail["code"] == "WEIGHT_EXPLORE_TOO_MANY_CANDIDATES"
    finally:
        get_settings().max_weight_explore_candidates = prev
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()


# ============================ 成功启动 + 复用批次端点 ============================
async def test_start_creates_explore_batch_with_per_candidate_members(monkeypatch):
    monkeypatch.setattr("app.api.weight_batches.get_rest_client", lambda: _FakeRest())
    await _reset()
    sm = get_sessionmaker()
    try:
        # 2 个子集组合（[a,b] / [a,c]）× 1 方法 = 2 候选。
        async with sm() as s:
            res = await start_weight_exploration(
                _explore_req(strategy="manual", params={"combos": [[0, 1], [0, 2]]}), session=s)
        assert res["kind"] == "explore" and len(res["candidates"]) == 2
        assert {c["variantLabel"] for c in res["candidates"]} == {"C0", "C1"}
        assert all(c["comboLabel"] for c in res["candidates"])   # 组合展示名非空
        bid, pid = res["batchId"], res["parentModelId"]

        # 父模型 source="batch"（主列表过滤）；批次 kind="explore" + explore_plan_json 落库。
        async with sm() as s:
            parent = await repo.get_weight_model(s, pid)
            batch = await repo.get_weight_batch(s, bid)
            runs = await repo.list_batch_runs(s, bid)
        assert parent.source == "batch"
        assert batch.kind == "explore"
        plan = json.loads(batch.explore_plan_json)
        assert plan["strategy"] == "manual" and len(plan["combos"]) == 2
        # 逐候选 members_json = 对应组合子集（C0=[a,b]，C1=[a,c]）。
        by_label = {r.variant_label: json.loads(r.members_json) for r in runs}
        assert [m["factorKey"] for m in by_label["C0"]] == ["mom_roc", "trend_ema_dist"]
        assert [m["factorKey"] for m in by_label["C1"]] == ["mom_roc", "rev_rsi"]

        await _await_batch_terminal(bid)
        # GET 详情复用批次端点：候选 members 回各自组合。
        async with sm() as s:
            detail = await get_weight_batch(bid, session=s)
        assert detail["kind"] == "explore" and detail["explorePlan"]["strategy"] == "manual"
        members_by_label = {c["variantLabel"]: c["members"] for c in detail["candidates"]}
        assert [m["factorKey"] for m in members_by_label["C0"]] == ["mom_roc", "trend_ema_dist"]
    finally:
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()


async def test_select_is_pointer_only_and_delete(monkeypatch):
    monkeypatch.setattr("app.api.weight_batches.get_rest_client", lambda: _FakeRest())
    await _reset()
    sm = get_sessionmaker()
    try:
        async with sm() as s:
            res = await start_weight_exploration(
                _explore_req(strategy="manual", params={"combos": [[0, 1], [0, 2]]}), session=s)
        bid, pid = res["batchId"], res["parentModelId"]
        await _await_batch_terminal(bid)
        async with sm() as s:
            detail = await get_weight_batch(bid, session=s)
            parent_before = json.loads((await repo.get_weight_model(s, pid)).members_json)
        succeeded = [c["id"] for c in detail["candidates"] if c["status"] == "succeeded"]
        assert succeeded, "至少一个候选应成功"
        # 择优：仅置 selected_run_id（指针），绝不据候选子集改写父模型 members_json（保持整池）。
        async with sm() as s:
            picked = await select_weight_batch_run(bid, WeightBatchSelectReq(runId=succeeded[0]), session=s)
            parent_after = json.loads((await repo.get_weight_model(s, pid)).members_json)
        assert picked["selectedRunId"] == succeeded[0]
        assert parent_after == parent_before                       # 父模型成员未被改写（异构候选不污染整池）
        assert [m["factorKey"] for m in parent_after] == ["mom_roc", "trend_ema_dist", "rev_rsi"]
        # DELETE 复用批次端点：级联删父模型 + 候选 + 批次行。
        async with sm() as s:
            resp = await delete_weight_batch(bid, session=s)
        assert resp.status_code == 204
        async with sm() as s:
            assert await repo.get_weight_batch(s, bid) is None
            assert await repo.get_weight_model(s, pid) is None
    finally:
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()


# ============================ 等价性硬门禁 + 按候选回测 ============================
async def _independent_run(members, method) -> dict:
    factor_service.clear_candle_cache()
    sm = get_sessionmaker()
    req = WeightTrainStartReq(**{**_RANGE, "members": members, "method": method,
                                 "config": {"normWindow": 100}, "horizon": 6})
    async with sm() as s:
        started = await wms.start_training(s, _FakeRest(), req)
    job = wtm.get_weight_train_manager().get(started["jobId"])
    for _ in range(600):
        if job is not None and job.is_terminal:
            break
        await asyncio.sleep(0.02)
    async with sm() as s:
        return await wms.get_run(s, started["modelId"], started["runId"])


async def test_candidate_equivalent_to_independent_subset_train_and_backtest(monkeypatch):
    monkeypatch.setattr("app.api.weight_batches.get_rest_client", lambda: _FakeRest())
    await _reset()
    sm = get_sessionmaker()
    try:
        # 探索：子集组合 C0=[a,b] / C1=[a,c] × linear = 2 候选（异构成员，非整池）。
        async with sm() as s:
            res = await start_weight_exploration(
                _explore_req(strategy="manual", params={"combos": [[0, 1], [0, 2]]}), session=s)
        bid, pid = res["batchId"], res["parentModelId"]
        await _await_batch_terminal(bid)
        async with sm() as s:
            runs = await repo.list_batch_runs(s, bid)
        c0 = next(r for r in runs if r.variant_label == "C0")
        assert c0.status == "succeeded"
        from app.schemas import weight_train_run_out
        cand = weight_train_run_out(c0, with_curve=False)

        # 独立 train 同一子集 [a,b] linear → 与候选 C0 字节级相等（绝不因批次编排 / 异构成员引入漂移）。
        indep = await _independent_run([_MEMBERS[0], _MEMBERS[1]], "linear")
        assert indep["status"] == "succeeded"
        assert cand["weights"] == indep["weights"]
        assert cand["normStats"] == indep["normStats"]
        assert cand["metrics"] == indep["metrics"]

        # 按候选回测：用候选自有组合成员（run.members_json = [a,b]）。若误用父模型整池 [a,b,c]，
        # assert_weights_cover 会 400 FACTOR_WEIGHTS_STALE；成功回测即证明取了候选自有成员。
        async with sm() as s:
            bt = await wms.backtest_run(s, _FakeRest(), pid, c0.id)
        assert bt is not None and "candles" in bt and "metrics" in bt
    finally:
        wtm._manager = None
        wbm._manager = None
        await dispose_engine()
