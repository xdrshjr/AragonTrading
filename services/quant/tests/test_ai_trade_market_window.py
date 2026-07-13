"""AI 决策背景高周期窗口测试。"""
from __future__ import annotations

from types import SimpleNamespace

from app.ai_auto.market_window import MarketWindow
from app.okx.types import BAR_SECONDS, Candle


class _Strategy:
    def warmup(self):
        return 20

    def htf_warmup_bars(self):
        return 60

    def timeframes(self):
        return ("15m", "4H")


def _window() -> MarketWindow:
    spec = SimpleNamespace(strategy=_Strategy())
    return MarketWindow("BTC-USDT-SWAP", "5m", [spec], priming_bars=40)


def _candle(ts: int, *, confirm: bool = True) -> Candle:
    return Candle(ts=ts, o=100, h=102, l=99, c=101, vol=10, confirm=confirm)


def test_policy_and_strategy_bars_share_one_deduplicated_window():
    window = _window()
    assert window._htf_bars == ["15m", "1H", "4H", "12H", "1D"]
    assert window._context_bars == ("1H", "4H", "12H", "1D")
    assert window.window_max >= 80


async def test_prefill_uses_raw_anchor_and_context_frames_hide_strategy_only_bar(monkeypatch):
    window = _window()
    calls = []

    async def fake_get(bar, limit):
        calls.append((bar, limit))
        seconds = BAR_SECONDS[bar]
        rows = [_candle(1_700_001_234 + index * seconds) for index in range(80)]
        rows.append(_candle(1_700_001_234 + 80 * seconds, confirm=False))
        return rows

    monkeypatch.setattr(window, "_safe_get_candles", fake_get)
    await window.prefill_htf(40)

    limits = dict(calls)
    assert all(limits[bar] >= 80 for bar in window._context_bars)
    assert all(len(rows) == 80 for rows in window._htf_windows.values())
    context = window.context_frames()
    assert list(context) == ["1H", "4H", "12H", "1D"]
    assert "15m" not in context
    context["1H"].loc[0, "c"] = -1
    assert window._htf_windows["1H"][0]["c"] == 101
    for bar in window._htf_bars:
        latest_raw_ts = 1_700_001_234 + 80 * BAR_SECONDS[bar]
        assert window._htf_next_refresh_ts[bar] == latest_raw_ts + BAR_SECONDS[bar]


async def test_refresh_uses_exchange_anchor_and_retries_empty_or_failed_fetch(monkeypatch):
    window = _window()
    bar = "1D"
    window._htf_bars = [bar]
    window._htf_windows = {bar: window._htf_windows[bar]}
    anchor = 1_700_001_234
    window._htf_next_refresh_ts = {bar: anchor}
    responses = [
        [],
        None,
        [_candle(anchor, confirm=False)],
        [_candle(anchor, confirm=True), _candle(anchor, confirm=True)],
    ]
    calls = []

    async def fake_get(request_bar, limit):
        calls.append((request_bar, limit))
        return responses.pop(0)

    monkeypatch.setattr(window, "_safe_get_candles", fake_get)
    await window.maybe_refresh_htf(anchor - 1)
    assert calls == []

    await window.maybe_refresh_htf(anchor)
    assert window._htf_next_refresh_ts[bar] == anchor
    await window.maybe_refresh_htf(anchor + 300)
    assert window._htf_next_refresh_ts[bar] == anchor
    await window.maybe_refresh_htf(anchor + 600)
    assert len(calls) == 3
    assert window._htf_next_refresh_ts[bar] == anchor + BAR_SECONDS[bar]
    assert len(window._htf_windows[bar]) == 0
    await window.maybe_refresh_htf(anchor + BAR_SECONDS[bar])
    assert len(calls) == 4
    assert len(window._htf_windows[bar]) == 1
    assert window._htf_windows[bar][0]["ts"] == anchor
