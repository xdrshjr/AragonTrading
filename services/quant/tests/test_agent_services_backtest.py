"""复用回测 helper 等价性回归护栏（与直接 run_backtest 结果一致，R8）。"""
from __future__ import annotations

import json

import pytest

from app.agent import services
from app.engine.backtester import run_backtest
from app.okx.types import Candle
from app.strategies.base import get_strategy_class
from tests.conftest import make_synthetic_candles


class _FakeRest:
    def __init__(self):
        self.calls = []

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        self.calls.append({"inst_id": inst_id, "bar": bar, "limit": limit,
                           "before": before, "after": after})
        rows = make_synthetic_candles(min(limit, 300), start_ts=1_700_000_000, step=3600)
        return [Candle(ts=r["ts"], o=r["o"], h=r["h"], l=r["l"], c=r["c"], vol=r["vol"])
                for r in rows]


@pytest.fixture
def db():
    from app.db.session import dispose_engine, get_sessionmaker, init_db
    return dispose_engine, get_sessionmaker, init_db


async def test_run_single_backtest_matches_direct_backtester(db):
    dispose_engine, get_sessionmaker, init_db = db
    await init_db()
    rest = _FakeRest()
    start_ts, end_ts = 1_700_000_000, 1_700_000_000 + 100 * 3600
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            from app.db import repositories as repo
            strategies = await repo.list_strategies(session)
            sid = next(s["id"] for s in strategies if not s.get("unavailable"))
            row = await repo.get_strategy_row(session, sid)
        cls = get_strategy_class(row.template_key)
        params = {**cls.meta.default_params, **json.loads(row.params_json or "{}")}

        async with sm() as session:
            result = await services.run_single_backtest(
                session, rest, strategy_id=sid, inst_id="BTC-USDT-SWAP", bar="1H",
                limit=200, start_ts=start_ts, end_ts=end_ts)

        # 直接用 backtester 跑同一批 candles，比对指标等价（无行为漂移）。
        candles = await rest.get_candles("BTC-USDT-SWAP", "1H", 200, before=end_ts, after=start_ts)
        candles = [c for c in candles if start_ts <= c.ts <= end_ts]
        direct = run_backtest(cls(params), candles, "1H", initial_capital=10000.0,
                              taker_fee_bps=5.0, slippage_bps=2.0, leverage=1.0)
        assert "runId" in result
        assert result["metrics"]["totalReturn"] == direct.metrics["totalReturn"]
        assert result["metrics"]["tradeCount"] == direct.metrics["tradeCount"]
    finally:
        await dispose_engine()


async def test_run_compare_returns_one_item_per_strategy(db):
    dispose_engine, get_sessionmaker, init_db = db
    await init_db()
    rest = _FakeRest()
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            from app.db import repositories as repo
            strategies = [s for s in await repo.list_strategies(session) if not s.get("unavailable")]
        ids = [strategies[0]["id"], strategies[1]["id"]]
        async with sm() as session:
            out = await services.run_compare(
                session, rest, strategy_ids=ids, inst_id="BTC-USDT-SWAP", bar="1H",
                limit=200, start_ts=1_700_000_000, end_ts=1_700_000_000 + 100 * 3600)
        assert len(out["items"]) == 2
        assert {it["strategyId"] for it in out["items"]} == set(ids)
    finally:
        await dispose_engine()
