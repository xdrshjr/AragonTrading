"""Agent 工具单测：summary/payload + ok=False（OKX 不可达不假数据，C1）。"""
from __future__ import annotations

import pytest

from app.agent.tools.base import ToolContext, ToolResult, UiContext
from app.agent.tools.market_tools import GetCandlesTool, GetMarketSummaryTool, ListInstrumentsTool
from app.agent.tools.registry import ToolRegistry
from app.agent.tools.run_tools import GetRunMetricsTool
from app.agent.tools.strategy_tools import CompareStrategiesTool, ListStrategiesTool, RunBacktestTool
from app.agent.tools.ui_tools import ShowChartTool
from app.okx.rest_client import OKXError
from app.okx.types import Candle, Instrument


class _FakeRest:
    def __init__(self, instruments=None, candles=None, raise_okx=False):
        self._instruments = instruments or []
        self._candles = candles or []
        self._raise = raise_okx

    async def get_instruments(self, inst_type="SWAP"):
        if self._raise:
            raise OKXError("OKX_UNREACHABLE", "无法连接 OKX")
        return self._instruments

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        if self._raise:
            raise OKXError("OKX_UNREACHABLE", "无法连接 OKX")
        return self._candles


def _ctx():
    from app.db.session import get_sessionmaker
    return ToolContext(session_factory=get_sessionmaker(), ui=UiContext(instId="BTC-USDT-SWAP", bar="1H"))


def _inst(inst_id, last, vol):
    return Instrument(instId=inst_id, instType="SPOT", base=inst_id.split("-")[0],
                      quote="USDT", last=last, open24h=last * 0.95,
                      vol24h=vol, volCcy24h=vol * last, changePct=0.0526)


async def test_list_instruments_truncates_and_summarizes(monkeypatch):
    fake = _FakeRest(instruments=[_inst(f"C{i}-USDT", 100 + i, 1000 - i) for i in range(30)])
    monkeypatch.setattr("app.agent.tools.market_tools.get_rest_client", lambda: fake)
    res = await ListInstrumentsTool().execute({"instType": "SPOT"}, _ctx())
    assert res.ok is True
    assert "C0-USDT" in res.summary
    assert len(res.payload["items"]) == 20  # 截断 Top 20


async def test_get_market_summary_found_and_missing(monkeypatch):
    fake = _FakeRest(instruments=[_inst("BTC-USDT", 50000, 10)])
    monkeypatch.setattr("app.agent.tools.market_tools.get_rest_client", lambda: fake)
    ok = await GetMarketSummaryTool().execute({"instId": "BTC-USDT"}, _ctx())
    assert ok.ok is True and ok.payload["last"] == 50000
    missing = await GetMarketSummaryTool().execute({"instId": "ZZZ-USDT"}, _ctx())
    assert missing.ok is False


async def test_get_candles_empty_is_not_faked(monkeypatch):
    fake = _FakeRest(candles=[])
    monkeypatch.setattr("app.agent.tools.market_tools.get_rest_client", lambda: fake)
    res = await GetCandlesTool().execute({"instId": "BTC-USDT-SWAP", "bar": "1H"}, _ctx())
    assert res.ok is False


async def test_show_chart_produces_directive():
    tool = ShowChartTool()
    assert tool.is_ui_action is True
    res = await tool.execute({"instId": "ETH-USDT", "bar": "1D", "runId": 7}, _ctx())
    assert res.ok is True
    assert res.payload == {"action": "show_chart", "instId": "ETH-USDT", "bar": "1D", "runId": 7}


async def test_registry_converts_okx_error_to_not_ok(monkeypatch):
    fake = _FakeRest(raise_okx=True)
    monkeypatch.setattr("app.agent.tools.market_tools.get_rest_client", lambda: fake)
    registry = ToolRegistry()
    res = await registry.execute("list_instruments", {"instType": "SWAP"}, _ctx())
    assert res.ok is False
    assert "OKX" in res.summary  # 如实告知不可达，绝不假数据（C1）


async def test_list_strategies_reads_db():
    from app.db.session import dispose_engine, init_db
    await init_db()
    try:
        res = await ListStrategiesTool().execute({}, _ctx())
        assert res.ok is True
        assert len(res.payload["items"]) >= 5
    finally:
        await dispose_engine()


async def test_registry_unknown_tool():
    registry = ToolRegistry()
    res = await registry.execute("does_not_exist", {}, _ctx())
    assert res.ok is False


# ============================ 入参边界校验（H2）：正反例 ============================
def test_validate_run_backtest_reject_and_accept():
    tool = RunBacktestTool()
    assert tool.validate({}) is not None                                       # 缺必填
    assert tool.validate({"strategyId": 1, "instId": "BTC-USDT-SWAP", "bar": "2H",
                          "startTs": 1, "endTs": 2}) is not None               # bar 越界
    assert tool.validate({"strategyId": 1, "instId": "BTC-USDT-SWAP", "bar": "1H",
                          "startTs": 5, "endTs": 5}) is not None               # ts 逆序/相等
    assert tool.validate({"strategyId": 1, "instId": "BTC-USDT-SWAP", "bar": "1H",
                          "startTs": 1, "endTs": 2, "limit": 99999}) is not None  # limit 越界
    assert tool.validate({"strategyId": 1, "instId": "BTC-USDT-SWAP", "bar": "1H",
                          "startTs": 1, "endTs": 2}) is None                   # 合法


def test_validate_compare_strategy_ids():
    base = {"instId": "ETH-USDT", "bar": "1H", "startTs": 1, "endTs": 2}
    assert CompareStrategiesTool().validate({**base, "strategyIds": []}) is not None
    assert CompareStrategiesTool().validate({**base, "strategyIds": [1, -2]}) is not None
    assert CompareStrategiesTool().validate({**base, "strategyIds": [1, 2]}) is None


def test_validate_get_run_metrics_runid():
    assert GetRunMetricsTool().validate({"runId": 0}) is not None
    assert GetRunMetricsTool().validate({"runId": -1}) is not None
    assert GetRunMetricsTool().validate({"runId": 3}) is None


def test_validate_list_instruments_enum():
    assert ListInstrumentsTool().validate({"instType": "BOGUS"}) is not None
    assert ListInstrumentsTool().validate({"instType": "SPOT"}) is None
    assert ListInstrumentsTool().validate({}) is None  # 缺省由 execute 回退默认


def test_validate_show_chart_all_optional():
    assert ShowChartTool().validate({}) is None
    assert ShowChartTool().validate({"bar": "9x"}) is not None
    assert ShowChartTool().validate({"startTs": 9, "endTs": 1}) is not None
    assert ShowChartTool().validate({"instId": "X-Y", "runId": 7}) is None


async def test_invalid_args_blocked_before_execute(monkeypatch):
    """非法入参经 validate 拦截，execute 不被调用（spy），summary 指明原因（H2 闭环）。"""
    called = {"n": 0}

    async def _spy_execute(self, args, ctx):
        called["n"] += 1
        return ToolResult(True, "should not run")

    monkeypatch.setattr(RunBacktestTool, "execute", _spy_execute)
    res = await ToolRegistry().execute("run_backtest", {"strategyId": -1}, _ctx())
    assert res.ok is False and "参数无效" in res.summary
    assert called["n"] == 0  # execute 未被调用


# ============================ 截断透明化（RS-15）：正反例 ============================
async def test_list_instruments_truncation_transparency(monkeypatch):
    big = _FakeRest(instruments=[_inst(f"C{i}-USDT", 100 + i, 1000 - i) for i in range(30)])
    monkeypatch.setattr("app.agent.tools.market_tools.get_rest_client", lambda: big)
    res = await ListInstrumentsTool().execute({"instType": "SPOT"}, _ctx())
    assert res.payload["truncated"] is True and res.payload["totalCount"] == 30
    assert "已截断" in res.summary

    small = _FakeRest(instruments=[_inst(f"D{i}-USDT", 100 + i, 1.0) for i in range(5)])
    monkeypatch.setattr("app.agent.tools.market_tools.get_rest_client", lambda: small)
    res2 = await ListInstrumentsTool().execute({"instType": "SPOT"}, _ctx())
    assert res2.payload["truncated"] is False and res2.payload["totalCount"] == 5
    assert "已截断" not in res2.summary


async def _seed_run(n_trades: int, n_equity: int) -> int:
    from app.db import repositories as repo
    from app.db.session import get_sessionmaker
    from app.engine.simulator import EquityRecord, TradeRecord
    sm = get_sessionmaker()
    async with sm() as session:
        run = await repo.create_run(session, strategy_id=1, inst_id="BTC-USDT-SWAP",
                                     bar="1H", mode="backtest", config={})
        rid = run.id
        for i in range(n_trades):
            await repo.append_trade(session, rid, TradeRecord(
                ts=1_700_000_000 + i, side="long", price=100.0 + i, qty=1.0, fee=0.1,
                pnl=0.0, position_after=1.0, reason="t"))
        for i in range(n_equity):
            await repo.append_equity(session, rid, EquityRecord(
                ts=1_700_000_000 + i, equity=10000.0 + i, drawdown=0.0))
        return rid


async def test_get_run_trades_truncation_transparency():
    from app.agent.tools.run_tools import GetRunTradesTool
    from app.db.session import dispose_engine, init_db
    await init_db()
    try:
        full = await _seed_run(60, 0)  # ≥ 截断上限 50 → truncated
        res = await GetRunTradesTool().execute({"runId": full}, _ctx())
        assert res.payload["truncated"] is True and res.payload["count"] == 50
        assert "已截断" in res.summary

        few = await _seed_run(3, 0)
        res2 = await GetRunTradesTool().execute({"runId": few}, _ctx())
        assert res2.payload["truncated"] is False and res2.payload["count"] == 3
        assert "已截断" not in res2.summary
    finally:
        await dispose_engine()


async def test_get_run_equity_downsample_transparency():
    from app.agent.tools.run_tools import GetRunEquityTool
    from app.db.session import dispose_engine, init_db
    await init_db()
    try:
        big = await _seed_run(0, 200)  # > 下采样目标 60 → downsampled
        res = await GetRunEquityTool().execute({"runId": big}, _ctx())
        assert res.payload["truncated"] is True and res.payload["totalCount"] == 200
        assert "已下采样" in res.summary

        small = await _seed_run(0, 10)
        res2 = await GetRunEquityTool().execute({"runId": small}, _ctx())
        assert res2.payload["truncated"] is False and res2.payload["totalCount"] == 10
        assert "已下采样" not in res2.summary
    finally:
        await dispose_engine()

