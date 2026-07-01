"""权重模型「一键回测」端到端守门 + 契约（weight-model-backtest-replay §七）。

复用 patch_okx（monkeypatch ``app.api.weight_models.get_rest_client`` 注桩行情，仿既有权重测试）与共享
进程 DB 纪律（按返回 id 断言、勿断言全局计数，见 aragon-quant-test-isolation）。回测同步、一次性、不落库：
组装成功训练记录为 factor_score 快照后复用既有 ``run_backtest`` 链路，故本测钉死打包体契约与各错误路径。
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
_METRIC_KEYS = {"totalReturn", "winRate", "profitFactor", "maxDrawdown", "sharpe", "tradeCount", "avgPnl"}


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


async def _train_succeeded(client: AsyncClient) -> dict:
    """跑一次真实流式训练到 succeeded，返回 ``{jobId, runId, modelId}``。"""
    started = (await client.post("/api/weight-models/train", json={
        **_RANGE, "members": _MEMBERS, "method": "linear",
        "config": {"normWindow": 100}, "horizon": 6})).json()
    run = await _wait_run(client, started["modelId"], started["runId"])
    assert run["status"] == "succeeded" and run["weights"], run
    return started


async def test_backtest_succeeds_returns_full_contract(patch_okx):
    """成功回测：candles/equity 非空、metrics 恰 7 键、meta 自洽；trades 为去 ORM 字段的 camelCase 打包。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            started = await _train_succeeded(client)
            resp = await client.post(
                f"/api/weight-models/{started['modelId']}/runs/{started['runId']}/backtest", json={})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["candles"], "回测须返回真实区间 K 线"
            assert body["equity"], "回测须返回净值序列"
            assert set(body["metrics"]) == _METRIC_KEYS
            assert body["meta"]["instId"] == "BTC-USDT-SWAP" and body["meta"]["bar"] == "1H"
            assert body["meta"]["startTs"] == _RANGE["startTs"] and body["meta"]["endTs"] == _RANGE["endTs"]
            assert body["meta"]["effectiveCandleCount"] == len(body["candles"])
            for trade in body["trades"]:
                assert {"ts", "side", "price", "qty", "pnl", "positionAfter"} <= set(trade)
                assert "id" not in trade and "runId" not in trade  # 内存态打包，无 ORM 独有字段
    finally:
        await dispose_engine()


async def test_backtest_404_missing_model(patch_okx):
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            resp = await client.post("/api/weight-models/999999/runs/1/backtest", json={})
            assert resp.status_code == 404
            assert resp.json()["error"]["code"] == "NOT_FOUND"
    finally:
        await dispose_engine()


async def test_backtest_404_missing_run(patch_okx):
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            started = await _train_succeeded(client)
            resp = await client.post(
                f"/api/weight-models/{started['modelId']}/runs/{started['runId'] + 987654}/backtest",
                json={})
            assert resp.status_code == 404
            assert resp.json()["error"]["code"] == "NOT_FOUND"
    finally:
        await dispose_engine()


async def test_backtest_409_non_succeeded_run(patch_okx):
    """对一条 running 记录回测 → 409 WEIGHT_RUN_NOT_BACKTESTABLE（非成功 / 无权重不可回测）。"""
    from app.db import repositories as repo
    from app.db.session import dispose_engine, get_sessionmaker
    client = await _client()
    try:
        async with client:
            started = await _train_succeeded(client)
            model_id = started["modelId"]
            sm = get_sessionmaker()
            async with sm() as session:
                run = await repo.add_weight_run(session, start=repo.WeightRunStart(
                    model_id=model_id, method="linear", inst_id="BTC-USDT-SWAP", bar="1H",
                    start_ts=_RANGE["startTs"], end_ts=_RANGE["endTs"], horizon=6,
                    config={"normWindow": 100}, started_ts=1_700_000_000))
                running_id = run.id
            resp = await client.post(
                f"/api/weight-models/{model_id}/runs/{running_id}/backtest", json={})
            assert resp.status_code == 409
            assert resp.json()["error"]["code"] == "WEIGHT_RUN_NOT_BACKTESTABLE"
    finally:
        await dispose_engine()


async def test_backtest_400_stale_members_after_edit(patch_okx):
    """训练成功后改成员键集 → 旧记录权重陈旧 → 400 FACTOR_WEIGHTS_STALE。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            started = await _train_succeeded(client)
            model_id, run_id = started["modelId"], started["runId"]
            put = await client.put(f"/api/weight-models/{model_id}", json={
                "members": [{"factorKey": "mom_roc", "params": {"win": 20}, "direction": "long"},
                            {"factorKey": "trend_ema_dist", "params": {}, "direction": "long"}]})
            assert put.status_code == 200, put.text  # 键集由 3 → 2，训练权重键集不再匹配
            resp = await client.post(
                f"/api/weight-models/{model_id}/runs/{run_id}/backtest", json={})
            assert resp.status_code == 400
            assert resp.json()["error"]["code"] == "FACTOR_WEIGHTS_STALE"
    finally:
        await dispose_engine()


async def test_backtest_422_start_without_end(patch_okx):
    """both-or-neither：只给 startTs 不给 endTs → 422（请求边界校验，先于 handler）。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            resp = await client.post("/api/weight-models/1/runs/1/backtest",
                                     json={"startTs": 1_700_000_000})
            assert resp.status_code == 422
    finally:
        await dispose_engine()


async def test_backtest_422_start_ge_end(patch_okx):
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            resp = await client.post("/api/weight-models/1/runs/1/backtest",
                                     json={"startTs": 2, "endTs": 1})
            assert resp.status_code == 422
    finally:
        await dispose_engine()
