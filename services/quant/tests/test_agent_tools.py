"""Agent 工具单测：summary/payload + ok=False（OKX 不可达不假数据，C1）。"""
from __future__ import annotations

import pytest

from app.agent.tools.base import ToolContext, UiContext
from app.agent.tools.market_tools import GetCandlesTool, GetMarketSummaryTool, ListInstrumentsTool
from app.agent.tools.registry import ToolRegistry
from app.agent.tools.strategy_tools import ListStrategiesTool
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
