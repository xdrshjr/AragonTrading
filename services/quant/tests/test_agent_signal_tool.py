"""test_signal_forward_returns 工具级单测：假 OKX → summary 统计+脚手架 / forming 剔除 / 不假数据。

复用 ``test_agent_regime_tool.py`` 的 ``_FakeRest`` + monkeypatch 范式（patch
``app.agent.tools.signal_tools.get_rest_client``）；离线、不触网络 (spec §6.1)。
"""
from __future__ import annotations

from app.agent.tools.base import ToolContext, UiContext
from app.agent.tools.registry import ToolRegistry
from app.agent.tools.signal_tools import TestSignalForwardReturnsTool
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
    return ToolContext(session_factory=lambda: None,
                       ui=UiContext(instId="BTC-USDT-SWAP", bar="1H"))


def _breakout_series(n=120, confirm_last=True):
    """上行 + 周期放量：每个放量根放量突破前高，制造多次 volume_breakout 触发。"""
    out, prev = [], 100.0
    for i in range(n):
        c = 100.0 + 0.6 * i
        vol = 3000.0 if i % 3 == 0 else 1000.0
        out.append(Candle(ts=_START + i * _STEP, o=prev, h=max(prev, c) + 0.5,
                          l=min(prev, c) - 0.5, c=c, vol=vol,
                          confirm=(confirm_last or i < n - 1)))
        prev = c
    return out


def _args(**extra):
    base = {"instId": "BTC-USDT-SWAP", "bar": "1H", "signal": "volume_breakout"}
    base.update(extra)
    return base


async def test_summary_has_stats_and_scaffold(monkeypatch):
    monkeypatch.setattr("app.agent.tools.signal_tools.get_rest_client",
                        lambda: _FakeRest(_breakout_series()))
    res = await TestSignalForwardReturnsTool().execute(_args(), _ctx())
    assert res.ok is True
    assert "h=1" in res.summary and "胜率" in res.summary and "t=" in res.summary
    assert "edge" in res.summary
    assert all(step in res.summary for step in ("第①步", "第②步", "第③步"))
    assert res.payload["signal"] == "volume_breakout"
    assert res.payload["direction"] == "long"
    assert res.payload["candidateStrategies"]
    assert any(c["key"] in res.summary for c in res.payload["candidateStrategies"])


async def test_custom_horizons(monkeypatch):
    monkeypatch.setattr("app.agent.tools.signal_tools.get_rest_client",
                        lambda: _FakeRest(_breakout_series()))
    res = await TestSignalForwardReturnsTool().execute(_args(horizons=[5, 2]), _ctx())
    assert res.ok is True
    assert [h["h"] for h in res.payload["horizons"]] == [2, 5]   # 去重 + 升序归一（RV-4）


async def test_cost_bps_adds_net_line(monkeypatch):
    monkeypatch.setattr("app.agent.tools.signal_tools.get_rest_client",
                        lambda: _FakeRest(_breakout_series()))
    res = await TestSignalForwardReturnsTool().execute(_args(costBps=5), _ctx())
    assert res.ok is True
    assert "扣 5bps" in res.summary
    assert res.payload["costBps"] == 5.0


async def test_drops_forming_candle(monkeypatch):
    candles = _breakout_series(confirm_last=False)
    monkeypatch.setattr("app.agent.tools.signal_tools.get_rest_client", lambda: _FakeRest(candles))
    res = await TestSignalForwardReturnsTool().execute(_args(), _ctx())
    assert res.ok is True
    confirmed = [c for c in candles if c.confirm]
    assert res.payload["lastTs"] == confirmed[-1].ts
    assert res.payload["lastTs"] != candles[-1].ts


async def test_empty_or_unreachable_not_faked(monkeypatch):
    monkeypatch.setattr("app.agent.tools.signal_tools.get_rest_client", lambda: _FakeRest(candles=[]))
    empty = await ToolRegistry().execute("test_signal_forward_returns", _args(), _ctx())
    assert empty.ok is False   # 数据不足，绝不补零假统计（C1）

    monkeypatch.setattr("app.agent.tools.signal_tools.get_rest_client",
                        lambda: _FakeRest(raise_okx=True))
    unreachable = await ToolRegistry().execute("test_signal_forward_returns", _args(), _ctx())
    assert unreachable.ok is False
    assert "OKX" in unreachable.summary


async def test_zero_occurrence_is_ok(monkeypatch):
    flat = [Candle(ts=_START + i * _STEP, o=100.0, h=100.5, l=99.5, c=100.0, vol=1000.0)
            for i in range(120)]
    monkeypatch.setattr("app.agent.tools.signal_tools.get_rest_client", lambda: _FakeRest(flat))
    res = await TestSignalForwardReturnsTool().execute(_args(), _ctx())
    assert res.ok is True   # 信号从未触发是合法事实，非错误（C1）
    assert res.payload["occurrences"] == 0
    assert "触发 0 次" in res.summary


def test_registered_in_registry():
    reg = ToolRegistry()
    assert reg.get("test_signal_forward_returns") is not None
    assert any(s.name == "test_signal_forward_returns" for s in reg.schemas())


def test_validate_reject_and_accept():
    tool = TestSignalForwardReturnsTool()
    assert tool.validate({}) is not None                                          # 缺 instId
    assert tool.validate({"instId": "BTC-USDT-SWAP", "bar": "1H"}) is not None    # 缺 signal
    assert tool.validate(_args(bar="2H")) is not None                             # bar 越界
    assert tool.validate({"instId": "BTC-USDT-SWAP", "bar": "1H",
                          "signal": "nope"}) is not None                          # 未知 signal
    assert tool.validate(_args(horizons=[0, 3])) is not None                      # 含非正整数
    assert tool.validate(_args(horizons=[1, 2, 3, 4, 5, 6, 7, 8, 9])) is not None  # 超 8 个
    assert tool.validate(_args(horizons=[200])) is not None                       # 元素越界
    assert tool.validate(_args(entryAt="weird")) is not None                      # entryAt 越界
    assert tool.validate(_args(startTs=9, endTs=1)) is not None                   # ts 逆序
    assert tool.validate(_args(limit=9999)) is not None                           # limit 越界
    assert tool.validate(_args()) is None                                         # 合法
    assert tool.validate(_args(horizons=[1, 3, 6, 12], entryAt="signal_close",
                              costBps=3, startTs=1, endTs=2, limit=300)) is None  # 合法全参
