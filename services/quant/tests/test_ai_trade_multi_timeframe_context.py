"""AI 自动交易多周期决策背景的纯函数测试。"""
from __future__ import annotations

import pandas as pd
import pytest

from app.ai_auto.multi_timeframe_context import (
    build_multi_timeframe_context,
    decision_timeframes,
)
from app.config import Settings


def _frame(bar_seconds: int, *, count: int = 80, step: float = 1.0) -> pd.DataFrame:
    rows = []
    for index in range(count):
        close = 100.0 + index * step
        rows.append({
            "ts": 1_700_000_000 + index * bar_seconds,
            "o": close - step,
            "h": close + 2.0,
            "l": close - 2.0,
            "c": close,
            "vol": 100.0 + index,
        })
    return pd.DataFrame(rows)


def test_decision_timeframes_follow_execution_policy():
    assert decision_timeframes("5m") == ("1H", "4H", "12H", "1D")
    assert decision_timeframes("1H") == ("4H", "12H", "1D")
    assert decision_timeframes("4H") == ("12H", "1D")
    assert decision_timeframes("1D") == ()
    with pytest.raises(ValueError):
        decision_timeframes("12H")


def test_builder_filters_future_rows_and_reports_missing_bars():
    frame = _frame(3600)
    visible_close = int(frame.iloc[-2]["ts"]) + 3600
    result = build_multi_timeframe_context(
        execution_bar="5m",
        frames={"1H": frame},
        as_of_ts=visible_close,
        last_px=float(frame.iloc[-2]["c"]),
        settings=Settings(),
    )

    assert result is not None
    assert [item["bar"] for item in result["frames"]] == ["1H"]
    assert result["frames"][0]["sampleCount"] == 79
    assert result["frames"][0]["closeTs"] == visible_close
    assert result["missingBars"] == ["1D", "12H", "4H"]


def test_close_boundary_is_inclusive_and_future_changes_do_not_leak(monkeypatch):
    frame = _frame(3600, count=2)

    monkeypatch.setattr(
        "app.ai_auto.multi_timeframe_context.build_market_context",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "app.ai_auto.multi_timeframe_context.build_market_digest",
        lambda df, *_args: {"lastClose": float(df.iloc[-1]["c"])},
    )
    first_close = int(frame.iloc[0]["ts"]) + 3600
    before = build_multi_timeframe_context(
        execution_bar="5m", frames={"1H": frame}, as_of_ts=first_close - 1,
        last_px=100.0, settings=Settings(),
    )
    exact = build_multi_timeframe_context(
        execution_bar="5m", frames={"1H": frame}, as_of_ts=first_close,
        last_px=100.0, settings=Settings(),
    )
    changed = frame.copy()
    changed.loc[1, ["o", "h", "l", "c"]] = [999.0, 1001.0, 998.0, 1000.0]
    replay = build_multi_timeframe_context(
        execution_bar="5m", frames={"1H": changed}, as_of_ts=first_close,
        last_px=100.0, settings=Settings(),
    )

    assert before["frames"] == []
    assert exact["frames"][0]["sampleCount"] == 1
    assert replay == exact


def test_dirty_or_failed_timeframe_does_not_block_other_frames(monkeypatch):
    frames = {
        "1H": _frame(3600),
        "4H": _frame(14400),
        "12H": _frame(43200),
        "1D": _frame(86400),
    }
    frames["1H"]["vol"] = 0.0
    frames["12H"]["c"] = float("nan")

    def fake_context(_frame, bar, _settings):
        if bar == "4H":
            raise RuntimeError("isolated failure")
        return {"primary": "range"}

    monkeypatch.setattr(
        "app.ai_auto.multi_timeframe_context.build_market_context", fake_context)
    monkeypatch.setattr(
        "app.ai_auto.multi_timeframe_context.build_market_digest",
        lambda *_args: None,
    )
    as_of_ts = max(int(frame.iloc[-1]["ts"]) + seconds
                   for frame, seconds in zip(frames.values(), (3600, 14400, 43200, 86400)))
    result = build_multi_timeframe_context(
        execution_bar="5m", frames=frames, as_of_ts=as_of_ts,
        last_px=100.0, settings=Settings(),
    )

    assert [item["bar"] for item in result["frames"]] == ["1D", "1H"]
    assert result["missingBars"] == ["12H", "4H"]


def test_builder_aggregates_trend_rsi_and_candidate_levels(monkeypatch):
    frames = {bar: _frame(seconds) for bar, seconds in {
        "1H": 3600,
        "4H": 14400,
        "12H": 43200,
        "1D": 86400,
    }.items()}

    def fake_context(_frame, bar, _settings):
        primary = {"1H": "range", "4H": "trend_down", "12H": "trend_up",
                   "1D": "trend_up"}[bar]
        return {"primary": primary}

    def fake_digest(_frame, bar, _settings):
        values = {
            "1H": ("neutral", 94.0, 108.0),
            "4H": ("oversold", 90.0, 106.0),
            "12H": ("overbought", 95.0, 110.0),
            "1D": ("overbought", 95.0, 120.0),
        }
        label, low, high = values[bar]
        return {
            "priceAction": {"swingLow": low, "swingHigh": high},
            "factors": {"rsi14": {"label": label}},
        }

    monkeypatch.setattr(
        "app.ai_auto.multi_timeframe_context.build_market_context", fake_context)
    monkeypatch.setattr(
        "app.ai_auto.multi_timeframe_context.build_market_digest", fake_digest)
    as_of_ts = max(int(frame.iloc[-1]["ts"]) + seconds
                   for frame, seconds in zip(frames.values(), (3600, 14400, 43200, 86400)))
    result = build_multi_timeframe_context(
        execution_bar="5m", frames=frames, as_of_ts=as_of_ts,
        last_px=100.0, settings=Settings(),
    )

    assert [item["bar"] for item in result["frames"]] == ["1D", "12H", "4H", "1H"]
    assert result["trendAlignment"] == {
        "status": "mixed",
        "bullishBars": ["1D", "12H"],
        "bearishBars": ["4H"],
        "rangeBars": ["1H"],
    }
    assert result["rsiOverboughtBars"] == ["1D", "12H"]
    assert result["rsiOversoldBars"] == ["4H"]
    assert result["nearestSupport"] == {
        "price": 95.0, "distancePct": 5.0, "bars": ["1D", "12H"],
    }
    assert result["nearestResistance"] == {
        "price": 106.0, "distancePct": 6.0, "bars": ["4H"],
    }


def test_builder_distinguishes_disabled_from_all_missing():
    assert build_multi_timeframe_context(
        execution_bar="5m", frames={}, as_of_ts=1_700_000_000,
        last_px=100.0,
        settings=Settings(ai_trade_multi_timeframe_context=False),
    ) is None

    result = build_multi_timeframe_context(
        execution_bar="5m", frames={}, as_of_ts=1_700_000_000,
        last_px=100.0, settings=Settings(),
    )
    assert result["frames"] == []
    assert result["missingBars"] == ["1D", "12H", "4H", "1H"]


def test_builder_validates_internal_contract():
    with pytest.raises(ValueError):
        build_multi_timeframe_context(
            execution_bar="5m", frames={}, as_of_ts=1_700_000_000,
            last_px=0.0, settings=Settings(),
        )
    with pytest.raises(ValueError):
        build_multi_timeframe_context(
            execution_bar="5m", frames={}, as_of_ts=1.5,
            last_px=100.0, settings=Settings(),
        )
