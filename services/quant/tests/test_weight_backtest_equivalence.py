"""权重模型「一键回测」与既有 factor_score 策略回测的等价性护栏（weight-model-backtest-replay §七）。

守护核心不变量——**复用既有 run_backtest 链路、无第二套回测引擎**：同一 ``(成员 + 训练权重 + 阈值 +
normStats + 区间 + 成本)`` 下，``backtest_run`` 出的 metrics 与「保存为 factor_set → 伴生 factor_score 策略 →
POST /strategies/{id}/backtest」逐键一致。二者最终都实例化同一 factor_score 类跑同一 K 线，故理应字节级同结果。
共享进程 DB，按返回 id 断言（见 aragon-quant-test-isolation）。
"""
from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.okx.types import Candle
from tests.conftest import make_synthetic_candles

_METRIC_KEYS = {"totalReturn", "winRate", "profitFactor", "maxDrawdown", "sharpe", "tradeCount", "avgPnl"}
_RANGE = {"instId": "BTC-USDT-SWAP", "bar": "1H",
          "startTs": 1_700_000_000, "endTs": 1_700_000_000 + 295 * 3600}
_MEMBERS = [
    {"factorKey": "mom_roc", "params": {"win": 20}, "direction": "long"},
    {"factorKey": "trend_ema_dist", "params": {}, "direction": "long"},
    {"factorKey": "rev_rsi", "params": {}, "direction": "short"},
]


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
    monkeypatch.setattr("app.api.strategies.get_rest_client", lambda: fake)
    from app.factors import service
    service.clear_candle_cache()
    return fake


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


async def test_weight_backtest_metrics_match_companion_strategy(patch_okx):
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            started = (await client.post("/api/weight-models/train", json={
                **_RANGE, "members": _MEMBERS, "method": "linear",
                "config": {"normWindow": 100}, "horizon": 6})).json()
            model_id, run_id = started["modelId"], started["runId"]
            run = await _wait_run(client, model_id, run_id)
            assert run["status"] == "succeeded" and run["weights"], run

            # A) 权重模型一键回测（区间缺省回落训练区间，成本走 settings 默认）。
            a = (await client.post(
                f"/api/weight-models/{model_id}/runs/{run_id}/backtest", json={})).json()

            # B) 保存为 factor_set（同成员 + 同训练权重 + 同 normStats + 阈值窗口对齐）→ 伴生 factor_score 策略。
            saved = await client.post("/api/factor-sets", json={
                "name": "等价性组合", "members": _MEMBERS, "method": "linear",
                "modelJson": {"weights": run["weights"], "normStats": run["normStats"]},
                "metricsJson": run["metrics"], "config": {"thresholds": {"normWindow": 100}}})
            assert saved.status_code == 201, saved.text
            companion_id = saved.json()["strategyId"]
            assert companion_id is not None

            # 同标的 / 周期 / 区间 / 默认成本回测伴生策略。
            b = (await client.post(f"/api/strategies/{companion_id}/backtest", json={
                "instId": _RANGE["instId"], "bar": _RANGE["bar"],
                "startTs": _RANGE["startTs"], "endTs": _RANGE["endTs"]})).json()

            assert set(a["metrics"]) == _METRIC_KEYS
            assert a["metrics"]["tradeCount"] == b["metrics"]["tradeCount"]
            for key in _METRIC_KEYS:
                assert a["metrics"][key] == pytest.approx(b["metrics"][key], rel=1e-9, abs=1e-9), key
    finally:
        await dispose_engine()
