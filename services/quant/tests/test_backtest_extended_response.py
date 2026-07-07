"""回测响应附带专业扩展绩效指标的护栏（strategy-backtest-config-modal §2.6 / §6.1）。

校验 `run_single_backtest`（POST /strategies/{id}/backtest 的实现）在既有 `metrics` 七项契约
之外**加法式**回传 `extendedMetrics`（16 键齐全、类型正确），且旧 `metrics` 契约零漂移。
退化输入（极短区间）不抛异常、扩展项安全回落。复用与 test_agent_services_backtest.py 同口径的
`_FakeRest` 合成 K 线夹具（不触真实 OKX）。
"""
from __future__ import annotations

import pytest

from app.agent import services
from app.okx.types import Candle
from tests.conftest import make_synthetic_candles

# metrics 七项契约（回归护栏：不得增删改键）。
_METRICS_KEYS = {"totalReturn", "winRate", "profitFactor", "maxDrawdown", "sharpe",
                 "tradeCount", "avgPnl"}
# extendedMetrics 十六键契约（与 engine/metrics_ext.compute_extended_metrics 逐字对齐）。
_EXTENDED_KEYS = {"annualizedReturn", "annualizedVolatility", "sortino", "calmar",
                  "maxDrawdownBars", "maxDrawdownSecs", "timeInMarketPct", "avgWin",
                  "avgLoss", "payoffRatio", "largestWin", "largestLoss",
                  "maxConsecutiveWins", "maxConsecutiveLosses", "avgHoldBars", "tradesPerWeek"}


class _FakeRest:
    """合成 K 线源（升序、已确认）；before 游标向更早翻页由 load_range_candles 处理。"""

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        rows = make_synthetic_candles(min(limit, 300), start_ts=1_700_000_000, step=3600)
        return [Candle(ts=r["ts"], o=r["o"], h=r["h"], l=r["l"], c=r["c"], vol=r["vol"])
                for r in rows]


@pytest.fixture
def db():
    from app.db.session import dispose_engine, get_sessionmaker, init_db
    return dispose_engine, get_sessionmaker, init_db


async def test_backtest_response_carries_extended_metrics(db):
    """响应含 extendedMetrics 且键齐全、类型正确；metrics 七键契约不变。"""
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

        async with sm() as session:
            result = await services.run_single_backtest(
                session, rest, strategy_id=sid, inst_id="BTC-USDT-SWAP", bar="1H",
                limit=200, start_ts=start_ts, end_ts=end_ts)

        # 既有七项契约不漂移。
        assert set(result["metrics"].keys()) == _METRICS_KEYS
        # 扩展项加法式回传，十六键齐全。
        assert "extendedMetrics" in result
        ext = result["extendedMetrics"]
        assert set(ext.keys()) == _EXTENDED_KEYS
        # 数值类型正确（全部为数字，绝无 None）。
        for key, value in ext.items():
            assert isinstance(value, (int, float)), f"{key} is not numeric: {value!r}"
    finally:
        await dispose_engine()


async def test_extended_metrics_degrade_safely_on_short_range(db):
    """极短区间（单根 K 线级）不抛异常，扩展项安全回落数字（不出现 None）。"""
    dispose_engine, get_sessionmaker, init_db = db
    await init_db()
    rest = _FakeRest()
    # 只覆盖起始一根附近的极短区间。
    start_ts, end_ts = 1_700_000_000, 1_700_000_000 + 3600
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            from app.db import repositories as repo
            strategies = await repo.list_strategies(session)
            sid = next(s["id"] for s in strategies if not s.get("unavailable"))

        async with sm() as session:
            result = await services.run_single_backtest(
                session, rest, strategy_id=sid, inst_id="BTC-USDT-SWAP", bar="1H",
                limit=50, start_ts=start_ts, end_ts=end_ts)

        ext = result["extendedMetrics"]
        assert set(ext.keys()) == _EXTENDED_KEYS
        assert all(v is not None for v in ext.values())
    finally:
        await dispose_engine()
