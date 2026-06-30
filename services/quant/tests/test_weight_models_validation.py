"""权重训练守门边界校验测试（H-B5/R-01/R-04 + H-T5 样本不足端到端）。

纯校验器（``_validate_bar`` / ``_validate_train_config``）直接单测真实键口径；守门序与样本不足走
``start_training`` 全链路（真 manager + 临时 DB + 假 OKX），不 mock 状态机本身。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import delete

from app.db.models import WeightModel, WeightTrainRun
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.factors import service as factor_service
from app.factors import weight_model_service as wms
from app.factors.weight_model_service import _validate_bar, _validate_train_config
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


def _req(**extra) -> WeightTrainStartReq:
    return WeightTrainStartReq(**{**_RANGE, "members": _MEMBERS, "method": "linear",
                                  "config": {"normWindow": 100}, "horizon": 6, **extra})


async def _fresh():
    await init_db()
    factor_service.clear_candle_cache()
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(delete(WeightTrainRun))
        await s.execute(delete(WeightModel))
        await s.commit()
    return sm


async def _await_terminal(job_id: str, tries: int = 400) -> None:
    job = get_weight_train_manager().get(job_id)
    for _ in range(tries):
        if job is not None and job.is_terminal:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("作业未在限定时间内到达终态")


# ---------------- 纯校验器：仅校验真实且出现的 camelCase 键（R-01） ----------------
def test_validate_bar_rejects_unknown_accepts_known():
    with pytest.raises(HTTPException) as ei:
        _validate_bar("9X")
    assert ei.value.status_code == 400 and ei.value.detail["code"] == "FACTOR_INVALID_BAR"
    _validate_bar("1H")   # 白名单周期不抛


def test_validate_config_rejects_real_keys_out_of_range():
    for bad in ({"split": 1.5}, {"split": 0.0}, {"split": "x"}, {"l2": -1.0},
                {"nEstimators": 1.5}, {"nEstimators": 0}, {"maxDepth": -2}):
        with pytest.raises(HTTPException) as ei:
            _validate_train_config(bad)
        assert ei.value.status_code == 400
        assert ei.value.detail["code"] == "FACTOR_TRAIN_INVALID_CONFIG"


def test_validate_config_keeps_clip_sentinel_and_ignores_norm_window():
    # clip<=0 是「关闭裁剪」的合法哨兵，绝不被拒；normWindow 不校验；缺省 / 合法值全过。
    _validate_train_config({"clip": 0})
    _validate_train_config({"clip": -4.0})
    _validate_train_config({"normWindow": 1})
    _validate_train_config({})
    _validate_train_config({"split": 0.7, "l2": 1.0, "nEstimators": 100, "maxDepth": 6})


# ---------------- 守门序与端到端（R-04 / H-T5） ----------------
async def test_illegal_bar_400_before_range_413():
    """R-04：非法 bar 在区间 413 之前被拦为 400（即便区间超长），杜绝非法 bar 抛无处理器的 500。"""
    sm = await _fresh()
    try:
        # 巨长区间（若 bar 合法会触发 413），但 bar 非法 → 必须先 400 FACTOR_INVALID_BAR。
        req = _req(bar="9X", endTs=1_700_000_000 + 10 ** 9)
        async with sm() as s:
            with pytest.raises(HTTPException) as ei:
                await wms.start_training(s, _FakeRest(), req)
        assert ei.value.status_code == 400 and ei.value.detail["code"] == "FACTOR_INVALID_BAR"
        # 守门零副作用：被拒请求无任何模型 / run 落库。
        async with sm() as s:
            assert await wms.list_models(s) == []
    finally:
        await dispose_engine()


async def test_invalid_config_400_zero_side_effect():
    """config 越界 → 400 FACTOR_TRAIN_INVALID_CONFIG，且守门零持久化副作用。"""
    sm = await _fresh()
    try:
        async with sm() as s:
            with pytest.raises(HTTPException) as ei:
                await wms.start_training(s, _FakeRest(), _req(config={"split": 2.0}))
        assert ei.value.status_code == 400 and ei.value.detail["code"] == "FACTOR_TRAIN_INVALID_CONFIG"
        async with sm() as s:
            assert await wms.list_models(s) == []
    finally:
        await dispose_engine()


async def test_insufficient_samples_end_to_end_fails_run():
    """H-T5：整体样本不足经 manager → run failed(WEIGHT_TRAIN_INSUFFICIENT_SAMPLES)（全链路，非仅引擎层）。"""
    sm = await _fresh()
    try:
        # normWindow=400 > 可用根数（295）→ 因果 z-score 全暖机 NaN → 对齐后 0 行 → 样本不足。
        async with sm() as s:
            started = await wms.start_training(s, _FakeRest(), _req(config={"normWindow": 400}))
        await _await_terminal(started["jobId"])
        async with sm() as s:
            run = await wms.get_run(s, started["modelId"], started["runId"])
        assert run["status"] == "failed"
        assert run["errorCode"] == "WEIGHT_TRAIN_INSUFFICIENT_SAMPLES"
    finally:
        await dispose_engine()
