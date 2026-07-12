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


async def test_run_single_backtest_risk_override_overrides_strategy_risk(db):
    """risk_override 覆盖策略自带风控（backtest-stop-loss-take-profit §6.1-3）。

    对同一策略、同一批 candles，紧止盈止损覆盖会在触发价强平 → 成交出现 stop_loss/take_profit 出场，
    且成交序列与不覆盖时不同；config_json.riskOverride 留痕本次覆盖（可复现、不写回策略行）。
    """
    dispose_engine, get_sessionmaker, init_db = db
    await init_db()
    rest = _FakeRest()
    start_ts, end_ts = 1_700_000_000, 1_700_000_000 + 200 * 3600
    try:
        sm = get_sessionmaker()
        from app.db import repositories as repo
        async with sm() as session:
            strategies = await repo.list_strategies(session)
            sid = next(s["id"] for s in strategies if s["templateKey"] == "ema_cross_scalper")

        async with sm() as session:
            base = await services.run_single_backtest(
                session, rest, strategy_id=sid, inst_id="BTC-USDT-SWAP", bar="1H",
                limit=300, start_ts=start_ts, end_ts=end_ts)
            base_trades = await repo.list_trades(session, base["runId"], limit=1000)

        async with sm() as session:
            over = await services.run_single_backtest(
                session, rest, strategy_id=sid, inst_id="BTC-USDT-SWAP", bar="1H",
                limit=300, start_ts=start_ts, end_ts=end_ts,
                risk_override={"stop_loss_pct": 0.001, "take_profit_pct": 0.001})
            over_trades = await repo.list_trades(session, over["runId"], limit=1000)
            over_run = await repo.get_run(session, over["runId"])

        # 覆盖后至少一笔以 stop_loss/take_profit 强平（紧止盈止损在触发价出场）。
        assert any(t.reason in {"stop_loss", "take_profit"} for t in over_trades)
        # 成交序列相对不覆盖时改变（覆盖真的生效，非空转）。
        assert [t.reason for t in over_trades] != [t.reason for t in base_trades]
        # config_json 留痕本次覆盖（可复现留痕）。
        cfg = json.loads(over_run.config_json or "{}")
        assert cfg.get("riskOverride") == {"stop_loss_pct": 0.001, "take_profit_pct": 0.001}
    finally:
        await dispose_engine()


async def test_run_single_backtest_none_risk_override_is_zero_regression(db):
    """risk_override=None 时与直接引擎输出等价（排除自增 runId 与落库时间戳，§6.1-4）。

    验证「注入通道未触发时无副作用」：metrics 逐字段一致、成交序列（reason/price）一致、净值序列
    长度与首末点一致；引擎风控本身另由 test_simulator.py 覆盖，此处不重复测引擎。
    """
    dispose_engine, get_sessionmaker, init_db = db
    await init_db()
    rest = _FakeRest()
    start_ts, end_ts = 1_700_000_000, 1_700_000_000 + 100 * 3600
    try:
        sm = get_sessionmaker()
        from app.db import repositories as repo
        async with sm() as session:
            strategies = await repo.list_strategies(session)
            sid = next(s["id"] for s in strategies if s["templateKey"] == "ema_cross_scalper")
            row = await repo.get_strategy_row(session, sid)
        cls = get_strategy_class(row.template_key)
        params = {**cls.meta.default_params, **json.loads(row.params_json or "{}")}

        async with sm() as session:
            result = await services.run_single_backtest(
                session, rest, strategy_id=sid, inst_id="BTC-USDT-SWAP", bar="1H",
                limit=200, start_ts=start_ts, end_ts=end_ts, risk_override=None)
            trades = await repo.list_trades(session, result["runId"], limit=1000)
            equity = await repo.list_equity(session, result["runId"])

        candles = await rest.get_candles("BTC-USDT-SWAP", "1H", 200, before=end_ts, after=start_ts)
        candles = [c for c in candles if start_ts <= c.ts <= end_ts]
        direct = run_backtest(cls(params), candles, "1H", initial_capital=10000.0,
                              taker_fee_bps=5.0, slippage_bps=2.0, leverage=1.0)
        # 指标逐字段一致（排除 runId / 时间戳）。
        assert result["metrics"] == direct.metrics
        # 成交序列（reason/price）与直接引擎一致——None 覆盖零副作用。
        assert [t.reason for t in trades] == [t.reason for t in direct.trades]
        assert [round(t.price, 6) for t in trades] == [round(t.price, 6) for t in direct.trades]
        # 净值序列长度与首末点一致。
        assert len(equity) == len(direct.equity_points)
        assert equity[0].equity == pytest.approx(direct.equity_points[0].equity)
        assert equity[-1].equity == pytest.approx(direct.equity_points[-1].equity)
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
