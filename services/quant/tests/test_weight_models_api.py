"""权重模型路由契约测试：CRUD、训练启动（jobId/runId/modelId）、守门零副作用（R-01）、去重（R-02）、

流式末步与 ``POST /factor-sets/train`` 等价（放行条件 1，API 级）。共享进程 DB，按返回 id 断言抗串扰。
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


def _train_body(**extra) -> dict:
    return {**_RANGE, "members": _MEMBERS, "method": "linear",
            "config": {"normWindow": 100}, "horizon": 6, **extra}


async def _wait_run(client: AsyncClient, model_id: int, run_id: int, tries: int = 300) -> dict:
    """轮询训练记录直到终态（succeeded/failed/cancelled）。"""
    for _ in range(tries):
        run = (await client.get(f"/api/weight-models/{model_id}/runs/{run_id}")).json()
        if run["status"] != "running":
            return run
        await asyncio.sleep(0.05)
    raise AssertionError("训练未在限定时间内到达终态")


async def test_model_crud_roundtrip(patch_okx):
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            created = await client.post("/api/weight-models", json={
                "name": "我的权重模型", "instId": "BTC-USDT-SWAP", "bar": "1H",
                "members": _MEMBERS, "method": "linear"})
            assert created.status_code == 201, created.text
            mid = created.json()["id"]
            got = await client.get(f"/api/weight-models/{mid}")
            assert got.status_code == 200 and got.json()["latestRun"] is None
            upd = await client.put(f"/api/weight-models/{mid}", json={"name": "改名", "enabled": False})
            assert upd.status_code == 200 and upd.json()["name"] == "改名"
            assert (await client.delete(f"/api/weight-models/{mid}")).status_code == 204
            assert (await client.get(f"/api/weight-models/{mid}")).status_code == 404
    finally:
        await dispose_engine()


async def test_train_streams_to_succeeded_and_updates_pointer(patch_okx):
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            started = await client.post("/api/weight-models/train", json=_train_body())
            assert started.status_code == 200, started.text
            body = started.json()
            assert set(body) == {"jobId", "runId", "modelId"}
            run = await _wait_run(client, body["modelId"], body["runId"])
            assert run["status"] == "succeeded"
            assert run["weights"] and run["curve"] and run["metrics"]
            assert run["curve"][-1]["fraction"] == 1.0
            model = (await client.get(f"/api/weight-models/{body['modelId']}")).json()
            assert model["latestRunId"] == body["runId"]
            assert model["bestValidIc"] is not None
    finally:
        await dispose_engine()


async def test_streaming_result_equals_sync_train(patch_okx):
    """放行条件 1（API 级）：流式末步 weights/metrics 与 POST /factor-sets/train 逐字段相等。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            sync = (await client.post("/api/factor-sets/train", json=_train_body())).json()
            started = (await client.post("/api/weight-models/train", json=_train_body())).json()
            run = await _wait_run(client, started["modelId"], started["runId"])
            assert run["weights"] == sync["weights"]
            assert run["normStats"] == sync["normStats"]
            assert run["metrics"] == sync["metrics"]
    finally:
        await dispose_engine()


async def test_gate_invalid_members_400_no_orphan(patch_okx):
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            before = len((await client.get("/api/weight-models")).json())
            r = await client.post("/api/weight-models/train",
                                  json=_train_body(members=[{"factorKey": "nope", "direction": "long"}]))
            assert r.status_code == 400
            after = len((await client.get("/api/weight-models")).json())
            assert after == before   # 守门零副作用：不建模型 / 不落 run
    finally:
        await dispose_engine()


async def test_gate_range_too_large_413_no_orphan(patch_okx):
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            before = len((await client.get("/api/weight-models")).json())
            huge = _train_body(startTs=1_600_000_000, endTs=1_600_000_000 + 20000 * 3600)
            r = await client.post("/api/weight-models/train", json=huge)
            assert r.status_code == 413 and r.json()["error"]["code"] == "FACTOR_TRAIN_TOO_LARGE"
            after = len((await client.get("/api/weight-models")).json())
            assert after == before
    finally:
        await dispose_engine()


async def test_gate_busy_429_no_orphan(patch_okx, monkeypatch):
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            before = len((await client.get("/api/weight-models")).json())

            class _BusyManager:
                def active_count(self):
                    return 99

                def reserve(self):   # H-B3：同步预占硬上限——繁忙时拒绝（返回 False → 429）
                    return False

                def release(self):
                    pass

            monkeypatch.setattr("app.factors.weight_model_service.get_weight_train_manager",
                                lambda: _BusyManager())
            r = await client.post("/api/weight-models/train", json=_train_body())
            assert r.status_code == 429 and r.json()["error"]["code"] == "WEIGHT_TRAIN_BUSY"
            after = len((await client.get("/api/weight-models")).json())
            assert after == before   # 并发守门命中亦零副作用
    finally:
        await dispose_engine()


async def test_factor_set_dedupe_reuses_model(patch_okx):
    """R-02：同 factorSetId 连续两次 train 复用同一 modelId（模型 +1 而非 +2，训练历史 +2）。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            first = (await client.post("/api/weight-models/train", json=_train_body(factorSetId=42))).json()
            await _wait_run(client, first["modelId"], first["runId"])
            second = (await client.post("/api/weight-models/train", json=_train_body(factorSetId=42))).json()
            await _wait_run(client, second["modelId"], second["runId"])
            assert first["modelId"] == second["modelId"]   # 复用同一模型
            runs = (await client.get(f"/api/weight-models/{first['modelId']}/runs")).json()
            assert len(runs) >= 2   # 训练历史累积
    finally:
        await dispose_engine()


async def test_run_detail_404_and_list_404(patch_okx):
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            assert (await client.get("/api/weight-models/999999")).status_code == 404
            assert (await client.get("/api/weight-models/999999/runs")).status_code == 404
            assert (await client.get("/api/weight-models/999999/runs/1")).status_code == 404
    finally:
        await dispose_engine()
