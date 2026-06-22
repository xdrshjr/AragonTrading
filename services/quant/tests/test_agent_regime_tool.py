"""label_market_regime 工具级单测：假 OKX → summary 证据+脚手架 / forming 剔除 / 不假数据。

复用 ``test_agent_tools.py`` 的 ``_FakeRest`` + monkeypatch 范式（patch
``app.agent.tools.regime_tools.get_rest_client``）；离线、不触网络 (spec §6.1)。
"""
from __future__ import annotations

from app.agent.tools.base import ToolContext, UiContext
from app.agent.tools.regime_tools import LabelMarketRegimeTool
from app.agent.tools.registry import ToolRegistry
from app.okx.rest_client import OKXError
from app.okx.types import Candle

_START = 1_700_000_000
_STEP = 3600


class _FakeRest:
    def __init__(self, candles=None, raise_okx=False):
        self._candles = candles or []
        self._raise = raise_okx

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        if self._raise:
            raise OKXError("OKX_UNREACHABLE", "无法连接 OKX")
        cs = self._candles
        if before is not None or after is not None:
            lo = after if after is not None else -1
            hi = before if before is not None else 10 ** 18
            cs = [c for c in cs if lo <= c.ts <= hi]
        return cs[-limit:] if len(cs) > limit else cs


def _ctx():
    # 本工具不开 DB session，传惰性 factory 即可（execute 从不调用它）。
    return ToolContext(session_factory=lambda: None,
                       ui=UiContext(instId="BTC-USDT-SWAP", bar="1H"))


def _series(closes, *, vols=None, confirm_last=True):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(Candle(ts=_START + i * _STEP, o=prev, h=max(prev, c) + 0.5,
                          l=min(prev, c) - 0.5, c=c, vol=vols[i] if vols else 1000.0,
                          confirm=(confirm_last or i < len(closes) - 1)))
        prev = c
    return out


def _uptrend(n=200, confirm_last=True):
    return _series([100.0 + 0.8 * i for i in range(n)], confirm_last=confirm_last)


def _up_then_down():
    closes = [100.0 + 0.8 * i for i in range(120)]
    last = closes[-1]
    closes += [last - 0.8 * (i + 1) for i in range(120)]
    return _series(closes)


async def test_snapshot_summary_has_evidence_and_scaffold(monkeypatch):
    monkeypatch.setattr("app.agent.tools.regime_tools.get_rest_client", lambda: _FakeRest(_uptrend()))
    res = await LabelMarketRegimeTool().execute({"instId": "BTC-USDT-SWAP", "bar": "1H"}, _ctx())
    assert res.ok is True
    assert "ER" in res.summary and "ATR%" in res.summary
    assert "primary=trend_up" in res.summary
    assert all(step in res.summary for step in ("第①步", "第②步", "第③步"))
    assert res.payload["primary"] == "trend_up"
    assert res.payload["candidateStrategies"]
    assert any(c["key"] in res.summary for c in res.payload["candidateStrategies"])


async def test_timeline_lists_segments(monkeypatch):
    monkeypatch.setattr("app.agent.tools.regime_tools.get_rest_client",
                        lambda: _FakeRest(_up_then_down()))
    res = await LabelMarketRegimeTool().execute(
        {"instId": "BTC-USDT-SWAP", "bar": "1H", "mode": "timeline"}, _ctx())
    assert res.ok is True
    assert res.payload["mode"] == "timeline"
    segments = res.payload["segments"]
    assert segments
    for seg in segments:
        assert seg["startTs"] < seg["endTs"]
    assert "子段" in res.summary
    assert all(step in res.summary for step in ("第①步", "第②步", "第③步"))


async def test_empty_or_unreachable_not_faked(monkeypatch):
    monkeypatch.setattr("app.agent.tools.regime_tools.get_rest_client", lambda: _FakeRest(candles=[]))
    empty = await ToolRegistry().execute(
        "label_market_regime", {"instId": "BTC-USDT-SWAP", "bar": "1H"}, _ctx())
    assert empty.ok is False  # 数据不足，绝不补零假判定（C1）

    monkeypatch.setattr("app.agent.tools.regime_tools.get_rest_client",
                        lambda: _FakeRest(raise_okx=True))
    unreachable = await ToolRegistry().execute(
        "label_market_regime", {"instId": "BTC-USDT-SWAP", "bar": "1H"}, _ctx())
    assert unreachable.ok is False
    assert "OKX" in unreachable.summary


async def test_snapshot_drops_forming_candle(monkeypatch):
    # 末根 confirm=False（forming）必须被剔除，判定末根为最后一根已确认 K 线（P1-4）。
    candles = _uptrend(n=220, confirm_last=False)
    monkeypatch.setattr("app.agent.tools.regime_tools.get_rest_client", lambda: _FakeRest(candles))
    res = await LabelMarketRegimeTool().execute({"instId": "BTC-USDT-SWAP", "bar": "1H"}, _ctx())
    assert res.ok is True
    confirmed = [c for c in candles if c.confirm]
    assert res.payload["lastTs"] == confirmed[-1].ts
    assert res.payload["lastTs"] != candles[-1].ts


def test_validate_reject_and_accept():
    tool = LabelMarketRegimeTool()
    assert tool.validate({}) is not None                                        # 缺 instId
    assert tool.validate({"instId": "BTC-USDT-SWAP"}) is not None               # 缺 bar
    assert tool.validate({"instId": "BTC-USDT-SWAP", "bar": "2H"}) is not None  # bar 越界
    assert tool.validate({"instId": "BTC-USDT-SWAP", "bar": "1H",
                          "mode": "weird"}) is not None                         # mode 越界
    assert tool.validate({"instId": "BTC-USDT-SWAP", "bar": "1H",
                          "startTs": 9, "endTs": 1}) is not None                # ts 逆序
    assert tool.validate({"instId": "BTC-USDT-SWAP", "bar": "1H",
                          "limit": 9999}) is not None                           # limit 越界
    assert tool.validate({"instId": "BTC-USDT-SWAP", "bar": "1H"}) is None      # 合法
    assert tool.validate({"instId": "BTC-USDT-SWAP", "bar": "1H", "mode": "timeline",
                          "startTs": 1, "endTs": 2, "limit": 300}) is None      # 合法 timeline
