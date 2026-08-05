"""取数辅助的**单一实现门禁**（discovery-asset-ledger §2.7）。

判据是行为式的：把 ``window_loader.load_window`` 换成必抛的桩之后，**一键回测**与**体检守护**
两条路径都必须失败。任何一条还能跑通，就说明它自己另有一份取数实现——两份口径（暖机根数、
高周期过滤、``max_bars`` 护栏）迟早漂移，而漂移了没人看得出来：同一条资产在一键回测与体检里
给出不同的数字，用户无从判断哪个是对的。
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.discovery import health_runtime, promote, window_loader


class _Sentinel(RuntimeError):
    """只有经过 ``load_window`` 才可能看到的异常。"""


class _Run:
    inst_id = "BTC-USDT-SWAP"
    bar = "1H"


class _Assembler:
    is_combo = False

    def build(self):
        raise AssertionError("单一实现门禁下不应走到装配")


@pytest.fixture
def broken_loader(monkeypatch):
    async def _boom(*_args, **_kwargs):
        raise _Sentinel("load_window stubbed")

    monkeypatch.setattr(window_loader, "load_window", _boom)
    monkeypatch.setattr(health_runtime.window_loader, "load_window", _boom)
    monkeypatch.setattr(health_runtime, "get_rest_client", lambda: object())
    return _boom


async def test_one_click_backtest_goes_through_the_single_loader(broken_loader,
                                                                 monkeypatch) -> None:
    monkeypatch.setattr(promote, "_required_timeframes", lambda _assembler: [])
    with pytest.raises(_Sentinel):
        await promote._load_backtest_window(object(), _Assembler(), _Run(), 0, 1,
                                            Settings())


async def test_health_guard_goes_through_the_single_loader(broken_loader) -> None:
    guard = health_runtime.HealthGuard()
    with pytest.raises(_Sentinel):
        await guard._load_group("BTC-USDT-SWAP", "1H", [], Settings())


async def test_loader_skips_lower_or_equal_timeframes(monkeypatch) -> None:
    """等于或低于基础周期的档一律忽略，且没有高周期需求时**一帧都不取**。"""
    seen: dict = {}

    async def _candles(_rest, **kwargs):
        seen["candles"] = kwargs
        return ["c"]

    async def _frames(_rest, **kwargs):
        seen["frames"] = kwargs
        return {"4H": []}

    from app.agent import services as agent_services

    monkeypatch.setattr(agent_services, "load_range_candles", _candles)
    monkeypatch.setattr(agent_services, "load_htf_frames", _frames)
    candles, frames = await window_loader.load_window(
        object(), inst_id="BTC-USDT-SWAP", bar="1H", start_ts=0, end_ts=100,
        timeframes=["1H", "15m"], settings=Settings())
    assert candles == ["c"] and frames == {}
    assert "frames" not in seen
    _candles_out, frames = await window_loader.load_window(
        object(), inst_id="BTC-USDT-SWAP", bar="1H", start_ts=0, end_ts=100,
        timeframes=["1H", "4H"], settings=Settings())
    assert frames == {"4H": []}
    assert seen["frames"]["timeframes"] == ["4H"]
