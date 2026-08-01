"""AI 决策背景高周期窗口 + 战术周期窗口测试。"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from app.ai_auto.market_window import MarketWindow, WindowOptions
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


# ---------- 战术周期窗口（ai-trade-wake-aware-context §6.1 T-3/T-15/T-16/T-20/T-21/T-23）----------
_TACTICAL_ANCHOR = 1_700_001_234


def _tactical_window(*, bars=("1m", "5m"), **opts) -> MarketWindow:
    spec = SimpleNamespace(strategy=_Strategy())
    return MarketWindow("BTC-USDT-SWAP", "30m", [spec], priming_bars=40,
                        options=WindowOptions(tactical_bars=bars, **opts))


def test_tactical_bars_are_stored_ascending_and_never_collide_with_htf_bars():
    window = _tactical_window(bars=("5m", "1m"))
    assert window.tactical_bars == ("1m", "5m")            # 构造期按 BAR_SECONDS 升序
    assert not set(window.tactical_bars) & set(window._htf_bars)


async def test_prefill_limit_stays_single_page_and_skips_unconfirmed_bars(monkeypatch):
    """T-23 + T-3：预填 limit ≤300（绝不触发 history-candles 翻页），且未确认根一律不入窗。"""
    window = _tactical_window(bars=("1m",), tactical_window_bars=300)
    calls = []

    async def fake_get(bar, limit):
        calls.append((bar, limit))
        rows = [_candle(_TACTICAL_ANCHOR + i * BAR_SECONDS[bar]) for i in range(50)]
        rows.append(_candle(_TACTICAL_ANCHOR + 50 * BAR_SECONDS[bar], confirm=False))
        return rows

    monkeypatch.setattr(window, "_safe_get_candles", fake_get)
    await window.prefill_tactical()

    assert calls == [("1m", 300)]                          # min(300, 300 + 5)
    assert len(window._tactical_windows["1m"]) == 50       # 未确认根被挡在窗外（杜绝 repaint）
    latest_raw = _TACTICAL_ANCHOR + 50 * BAR_SECONDS["1m"]
    assert window._tactical_next_refresh_ts["1m"] == latest_raw + BAR_SECONDS["1m"]


async def test_tactical_frames_are_defensive_copies(monkeypatch):
    window = _tactical_window(bars=("1m",))

    async def fake_get(bar, limit):
        return [_candle(_TACTICAL_ANCHOR + i * BAR_SECONDS[bar]) for i in range(40)]

    monkeypatch.setattr(window, "_safe_get_candles", fake_get)
    await window.prefill_tactical()
    frames = window.tactical_frames()
    assert list(frames) == ["1m"]
    frames["1m"].loc[0, "c"] = -1
    assert window._tactical_windows["1m"][0]["c"] == 101


async def test_single_tick_throttle_refreshes_at_most_one_timeframe(monkeypatch):
    """T-20：两个周期同时过锚点闸与配额闸时，一次调用只发一次 REST（取最细的那个），其余留给下一 tick。

    本方法跑在 5 秒盯市 tick 上，同一个 tick 还守着利润保护棘轮与会话止损这些真实资金路径。
    """
    window = _tactical_window(bars=("1m", "5m"), tactical_min_refresh_s=0.0)
    calls = []

    async def fake_get(bar, limit):
        calls.append(bar)
        return [_candle(_TACTICAL_ANCHOR)]

    monkeypatch.setattr(window, "_safe_get_candles", fake_get)
    await window.maybe_refresh_tactical(_TACTICAL_ANCHOR, 1000.0)
    assert calls == ["1m"]
    await window.maybe_refresh_tactical(_TACTICAL_ANCHOR, 1000.0)
    assert calls == ["1m", "5m"]                            # 下一 tick 才轮到另一个


async def test_quota_gate_caps_rest_calls_even_when_the_anchor_is_wrong(monkeypatch):
    """T-16：`min_refresh_s=20` 下连续 4 个 5 秒 tick 至多一次 REST（防多标的分组撞 429）。"""
    window = _tactical_window(bars=("1m",), tactical_min_refresh_s=20.0)
    calls = []

    async def fake_get(bar, limit):
        calls.append(bar)
        return None                                         # 取数失败：锚点不推进，只剩配额闸挡着

    monkeypatch.setattr(window, "_safe_get_candles", fake_get)
    for tick in range(4):
        await window.maybe_refresh_tactical(_TACTICAL_ANCHOR, 1000.0 + tick * 5)
    assert calls == ["1m"]
    await window.maybe_refresh_tactical(_TACTICAL_ANCHOR, 1000.0 + 25)
    assert calls == ["1m", "1m"]


async def test_failed_fetch_leaves_window_and_anchor_untouched(monkeypatch):
    """T-15：取数异常 ⇒ 窗口不变、锚点不推进（下 tick 重试），绝不假数据。"""
    window = _tactical_window(bars=("1m",), tactical_min_refresh_s=0.0)

    async def boom(bar, limit):
        raise RuntimeError("exchange down")

    monkeypatch.setattr(window, "_safe_get_candles", boom)
    with pytest.raises(RuntimeError):        # 桩绕过了 _safe_get_candles 自身的降级，验证外层不吞真异常
        await window.maybe_refresh_tactical(_TACTICAL_ANCHOR, 1000.0)

    async def empty(bar, limit):
        return None

    monkeypatch.setattr(window, "_safe_get_candles", empty)
    await window.maybe_refresh_tactical(_TACTICAL_ANCHOR, 2000.0)
    assert len(window._tactical_windows["1m"]) == 0
    assert "1m" not in window._tactical_next_refresh_ts


async def test_hanging_fetch_returns_within_the_hard_timeout(monkeypatch):
    """T-21：取数挂住 ⇒ `wait_for` 硬超时内返回、窗口不变、锚点不推进、无异常外泄。

    盯市循环守的是真实持仓——它的延迟绝不能为了取数让步。
    """
    window = _tactical_window(bars=("1m",), tactical_min_refresh_s=0.0,
                              tactical_fetch_timeout_s=0.05)

    async def hang(bar, limit):
        await asyncio.sleep(5)
        return [_candle(_TACTICAL_ANCHOR)]

    monkeypatch.setattr(window, "_safe_get_candles", hang)
    started = time.monotonic()
    await window.maybe_refresh_tactical(_TACTICAL_ANCHOR, 1000.0)
    assert time.monotonic() - started < 1.0
    assert len(window._tactical_windows["1m"]) == 0
    assert "1m" not in window._tactical_next_refresh_ts


async def test_no_tactical_bars_means_zero_rest_calls(monkeypatch):
    """总闸关闭 / 执行周期已是最细 ⇒ 窗口层零动作（逐字节回退）。"""
    window = _tactical_window(bars=())
    calls = []

    async def fake_get(bar, limit):
        calls.append(bar)
        return []

    monkeypatch.setattr(window, "_safe_get_candles", fake_get)
    await window.prefill_tactical()
    await window.maybe_refresh_tactical(_TACTICAL_ANCHOR, 1000.0)
    assert calls == []
    assert window.tactical_frames() == {}
