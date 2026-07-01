"""权重模型工作区跨特性端到端链路测试（G-T2）。

钉死「搭建 → 训练 → 回流 → 保存组合 → 伴生 factor_score 策略同步」全链路：流式训练产出真实权重 →
以该权重 + 成员 + 方法落库 factor_set（模拟前端「应用到组合并返回」的回流载荷）→ 单事务连带建一条
kind=factor / params.factorSetId 指向该组合的伴生策略（复用既有 factor_set ⇄ strategy 同步断言）。

各环节孤立有测，本例专防链路级回归（杜绝「训练正常、保存正常、但回流后伴生未同步」这类组合缺陷）。
共享进程 DB，按返回 id 断言抗串扰（呼应 aragon-quant-test-isolation）。
"""
from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.okx.types import Candle
from tests.conftest import make_synthetic_candles


class _FakeRest:
    async def health(self) -> bool:
        return True

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        rows = make_synthetic_candles(min(limit, 300), start_ts=1_700_000_000, step=3600)
        return [Candle(ts=r["ts"], o=r["o"], h=r["h"], l=r["l"], c=r["c"], vol=r["vol"]) for r in rows]


@pytest.fixture
def patch_okx(monkeypatch):
    fake = _FakeRest()
    monkeypatch.setattr("app.api.weight_models.get_rest_client", lambda: fake)
    monkeypatch.setattr("app.api.factors.get_rest_client", lambda: fake)
    from app.factors import service
    service.clear_candle_cache()
    return fake


_RANGE = {"instId": "BTC-USDT-SWAP", "bar": "1H",
          "startTs": 1_700_000_000, "endTs": 1_700_000_000 + 295 * 3600}
_MEMBERS = [
    {"factorKey": "mom_roc", "params": {"win": 20}, "direction": "long"},
    {"factorKey": "trend_ema_dist", "params": {}, "direction": "long"},
    {"factorKey": "rev_rsi", "params": {}, "direction": "short"},
]


async def _client() -> AsyncClient:
    from app.db.session import init_db
    await init_db()
    from app.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _wait_run(client: AsyncClient, model_id: int, run_id: int, tries: int = 300) -> dict:
    for _ in range(tries):
        run = (await client.get(f"/api/weight-models/{model_id}/runs/{run_id}")).json()
        if run["status"] != "running":
            return run
        await asyncio.sleep(0.05)
    raise AssertionError("训练未在限定时间内到达终态")


async def test_train_apply_save_combo_syncs_companion_strategy(patch_okx):
    """G-T2：流式训练成功 → 以产物回流保存组合 → 伴生 factor_score 策略已建且指向该组合。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            # 1) 搭建发起训练（origin=builder 的等价后端请求），跑到 succeeded。
            started = (await client.post("/api/weight-models/train", json={
                **_RANGE, "members": _MEMBERS, "method": "linear",
                "config": {"normWindow": 100}, "horizon": 6, "factorSetId": None})).json()
            run = await _wait_run(client, started["modelId"], started["runId"])
            assert run["status"] == "succeeded" and run["weights"]

            # 2) 回流 → 保存组合：把训练权重写入 modelJson（= 前端「应用到组合并返回」后保存）。
            saved = await client.post("/api/factor-sets", json={
                "name": "端到端组合", "members": _MEMBERS, "method": "linear",
                "modelJson": {"weights": run["weights"], "normStats": run["normStats"],
                              "trainerVersion": run["trainerVersion"], "provenance": run["provenance"]},
                "metricsJson": run["metrics"], "config": {"thresholds": {"normWindow": 100}}})
            assert saved.status_code == 201, saved.text
            body = saved.json()
            sid, companion_id = body["id"], body["strategyId"]

            # 3) 断言伴生 factor_score 策略已同步建出且指向该组合。
            assert companion_id is not None
            strategies = (await client.get("/api/strategies")).json()
            comp = next(s for s in strategies if s["id"] == companion_id)
            assert comp["kind"] == "factor" and comp["templateKey"] == "factor_score"
            assert comp["params"]["factorSetId"] == sid
            assert comp["name"] == "端到端组合 · 得分策略"
    finally:
        await dispose_engine()


async def test_train_save_then_delete_combo_removes_companion(patch_okx):
    """G-T2 反向闭环：删组合连带删伴生策略（链路对称，杜绝孤儿伴生）。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            started = (await client.post("/api/weight-models/train", json={
                **_RANGE, "members": _MEMBERS, "method": "linear",
                "config": {"normWindow": 100}, "horizon": 6})).json()
            run = await _wait_run(client, started["modelId"], started["runId"])
            resp = await client.post("/api/factor-sets", json={
                "name": "待删端到端组合", "members": _MEMBERS, "method": "linear",
                "modelJson": {"weights": run["weights"], "normStats": run["normStats"]},
                "config": {"thresholds": {"normWindow": 100}}})   # 阈值窗口须对齐训练 normStats（回流口径）
            assert resp.status_code == 201, resp.text
            saved = resp.json()
            sid, companion_id = saved["id"], saved["strategyId"]
            assert (await client.delete(f"/api/factor-sets/{sid}")).status_code == 204
            strategies = (await client.get("/api/strategies")).json()
            assert all(s["id"] != companion_id for s in strategies)
    finally:
        await dispose_engine()
