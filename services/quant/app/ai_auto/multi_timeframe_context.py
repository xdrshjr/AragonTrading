"""AI 决策级多周期市场背景（纯计算、严格因果）。"""
from __future__ import annotations

import math

import pandas as pd

from ..config import Settings
from ..okx.types import BAR_TO_WS_CHANNEL, BAR_SECONDS
from .market_context import build_market_context
from .market_digest import build_market_digest

_CONTEXT_POLICY = {
    "1m": ("1H", "4H", "12H", "1D"),
    "5m": ("1H", "4H", "12H", "1D"),
    "15m": ("1H", "4H", "12H", "1D"),
    "30m": ("1H", "4H", "12H", "1D"),
    "1H": ("4H", "12H", "1D"),
    "4H": ("12H", "1D"),
    "1D": (),
}
_OHLCV_COLUMNS = ("ts", "o", "h", "l", "c", "vol")


def decision_timeframes(execution_bar: str) -> tuple[str, ...]:
    """Return decision-context bars in ascending duration order."""
    if execution_bar not in BAR_TO_WS_CHANNEL:
        raise ValueError(f"unsupported AI execution bar: {execution_bar}")
    return _CONTEXT_POLICY[execution_bar]


def build_multi_timeframe_context(
    *,
    execution_bar: str,
    frames: dict[str, pd.DataFrame],
    as_of_ts: int,
    last_px: float,
    settings: Settings,
) -> dict | None:
    """Build causal multi-timeframe evidence for one AI decision instant."""
    policy = decision_timeframes(execution_bar)
    if type(as_of_ts) is not int:
        raise ValueError("as_of_ts must be an integer timestamp")
    if not math.isfinite(last_px) or last_px <= 0:
        raise ValueError("last_px must be positive and finite")
    if not settings.ai_trade_multi_timeframe_context or not policy:
        return None

    output_frames = []
    missing_bars = []
    stale_bars = []
    for bar in reversed(policy):
        item = _build_frame(bar, frames.get(bar), as_of_ts, settings)
        if item is None:
            missing_bars.append(bar)
            continue
        output_frames.append(item)
        if item["stale"]:
            stale_bars.append(bar)

    result = {
        "executionBar": execution_bar,
        "asOfTs": as_of_ts,
        "frames": output_frames,
        "trendAlignment": _trend_alignment(output_frames),
        "rsiOverboughtBars": _rsi_bars(output_frames, "overbought"),
        "rsiOversoldBars": _rsi_bars(output_frames, "oversold"),
        "nearestSupport": _candidate_level(output_frames, last_px, support=True),
        "nearestResistance": _candidate_level(output_frames, last_px, support=False),
        "missingBars": missing_bars,
        "staleBars": stale_bars,
    }
    return result


def _build_frame(
    bar: str,
    frame: pd.DataFrame | None,
    as_of_ts: int,
    settings: Settings,
) -> dict | None:
    try:
        causal = _causal_frame(frame, bar, as_of_ts)
        if causal.empty:
            return None
        regime = build_market_context(causal, bar, settings)
        digest = build_market_digest(causal, bar, settings)
        if regime is None and digest is None:
            return None
        close_ts = int(causal.iloc[-1]["ts"]) + BAR_SECONDS[bar]
        age_seconds = max(0, as_of_ts - close_ts)
        return {
            "bar": bar,
            "sampleCount": len(causal),
            "closeTs": close_ts,
            "ageSeconds": age_seconds,
            "stale": age_seconds >= 2 * BAR_SECONDS[bar],
            "regime": regime,
            "digest": digest,
        }
    except Exception:  # noqa: BLE001 -- one corrupt timeframe must not block the round
        return None


def _causal_frame(
    frame: pd.DataFrame | None,
    bar: str,
    as_of_ts: int,
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=_OHLCV_COLUMNS)
    clean = frame.loc[:, list(_OHLCV_COLUMNS)].copy()
    for column in _OHLCV_COLUMNS:
        clean[column] = pd.to_numeric(clean[column], errors="coerce")
    finite = clean[list(_OHLCV_COLUMNS)].apply(
        lambda column: column.map(math.isfinite)).all(axis=1)
    clean = clean.loc[finite]
    clean = clean.loc[clean["ts"] + BAR_SECONDS[bar] <= as_of_ts]
    return clean.sort_values("ts").drop_duplicates("ts", keep="last").reset_index(drop=True)


def _trend_alignment(frames: list[dict]) -> dict:
    bullish = _bars_with_primary(frames, "trend_up")
    bearish = _bars_with_primary(frames, "trend_down")
    ranges = _bars_with_primary(frames, "range")
    if bullish and bearish:
        status = "mixed"
    elif bullish:
        status = "bullish"
    elif bearish:
        status = "bearish"
    elif ranges:
        status = "range"
    else:
        status = "insufficient"
    return {
        "status": status,
        "bullishBars": bullish,
        "bearishBars": bearish,
        "rangeBars": ranges,
    }


def _bars_with_primary(frames: list[dict], primary: str) -> list[str]:
    return [item["bar"] for item in frames
            if (item.get("regime") or {}).get("primary") == primary]


def _rsi_bars(frames: list[dict], label: str) -> list[str]:
    bars = []
    for item in frames:
        digest = item.get("digest") or {}
        factors = digest.get("factors") or {}
        if (factors.get("rsi14") or {}).get("label") == label:
            bars.append(item["bar"])
    return bars


def _candidate_level(frames: list[dict], last_px: float, *, support: bool) -> dict | None:
    key = "swingLow" if support else "swingHigh"
    candidates: dict[float, list[str]] = {}
    for item in frames:
        digest = item.get("digest") or {}
        value = (digest.get("priceAction") or {}).get(key)
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            continue
        price = float(value)
        if (support and price > last_px) or (not support and price < last_px):
            continue
        candidates.setdefault(price, []).append(item["bar"])
    if not candidates:
        return None
    price = max(candidates) if support else min(candidates)
    return {
        "price": price,
        "distancePct": round(abs(price - last_px) / last_px * 100.0, 6),
        "bars": candidates[price],
    }
