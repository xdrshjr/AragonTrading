"""Causal range-bucket helpers for short-term range strategies."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..engine.indicators import atr, bollinger, rsi


RANGE_COLUMNS = [
    "range_high",
    "range_low",
    "range_mid",
    "range_width",
    "range_atr",
    "range_rsi",
    "range_boll_upper",
    "range_boll_ma",
    "range_boll_lower",
    "range_boll_width",
    "is_range_stable",
]


@dataclass(frozen=True)
class RangeStructureConfig:
    """Configuration for causal range-bucket structure detection."""

    range_seconds: int
    range_lookback: int
    boll_period: int
    boll_std_mult: float
    boll_width_atr_max: float
    rsi_period: int
    rsi_mid: float
    rsi_band: float
    atr_period: int
    width_atr_max: float


def resample_closed_buckets(df: pd.DataFrame, bucket_seconds: int) -> pd.DataFrame:
    """Aggregate sorted OHLCV rows into closed time buckets."""
    if df.empty:
        return pd.DataFrame(columns=["bucket", "o", "h", "l", "c", "vol"])

    buckets = (df["ts"] // int(bucket_seconds)).astype("int64")
    grouped = df.groupby(buckets, sort=True)
    out = pd.DataFrame({
        "bucket": grouped["ts"].first().index.astype("int64"),
        "o": grouped["o"].first().to_numpy(),
        "h": grouped["h"].max().to_numpy(),
        "l": grouped["l"].min().to_numpy(),
        "c": grouped["c"].last().to_numpy(),
        "vol": grouped["vol"].sum().to_numpy(),
    })
    return out.sort_values("bucket").reset_index(drop=True)


def attach_causal_range(
    df: pd.DataFrame,
    base_seconds: int,
    config: RangeStructureConfig,
) -> pd.DataFrame:
    """Attach shifted range-bucket columns to each base candle."""
    out = df.copy()
    if out.empty:
        for col in RANGE_COLUMNS:
            out[col] = pd.Series(dtype="float64")
        return out

    bucket_seconds = _effective_bucket_seconds(base_seconds, config.range_seconds)
    bucket_frame = resample_closed_buckets(out, bucket_seconds)
    per_bucket = _calculate_bucket_structure(bucket_frame, config)
    shifted = per_bucket.copy()
    shifted[RANGE_COLUMNS] = shifted[RANGE_COLUMNS].shift(1)
    base_buckets = (out["ts"] // bucket_seconds).astype("int64")
    mapped = shifted.set_index("bucket").reindex(base_buckets.to_numpy())
    mapped.index = out.index

    for col in RANGE_COLUMNS:
        out[col] = mapped[col]
    return out


def attach_execution_flags(
    df: pd.DataFrame,
    wick_ratio: float,
    touch_atr_mult: float,
) -> pd.DataFrame:
    """Attach 1m rejection flags near already-known range boundaries."""
    out = df.copy()
    body = (out["c"] - out["o"]).abs().clip(lower=1e-12)
    upper_wick = out["h"] - out[["o", "c"]].max(axis=1)
    lower_wick = out[["o", "c"]].min(axis=1) - out["l"]
    touch = float(touch_atr_mult) * out["range_atr"]

    out["upper_rejection"] = (
        (upper_wick >= float(wick_ratio) * body)
        & (out["h"] >= out["range_high"] - touch)
        & (out["c"] <= out["range_high"])
    )
    out["lower_rejection"] = (
        (lower_wick >= float(wick_ratio) * body)
        & (out["l"] <= out["range_low"] + touch)
        & (out["c"] >= out["range_low"])
    )
    return out


def _effective_bucket_seconds(base_seconds: int, range_seconds: int) -> int:
    """Use base bars as buckets when requested range is below the input bar."""
    base = max(1, int(base_seconds))
    requested = max(1, int(range_seconds))
    if base >= requested:
        return base
    return requested


def _calculate_bucket_structure(
    buckets: pd.DataFrame,
    config: RangeStructureConfig,
) -> pd.DataFrame:
    """Calculate unshifted range structure on bucket rows."""
    out = pd.DataFrame({"bucket": buckets["bucket"]})
    high = buckets["h"]
    low = buckets["l"]
    close = buckets["c"]
    lookback = int(config.range_lookback)

    out["range_high"] = high.rolling(lookback, min_periods=lookback).max()
    out["range_low"] = low.rolling(lookback, min_periods=lookback).min()
    out["range_mid"] = (out["range_high"] + out["range_low"]) / 2.0
    out["range_width"] = out["range_high"] - out["range_low"]
    out["range_atr"] = atr(buckets, int(config.atr_period))
    out["range_rsi"] = rsi(close, int(config.rsi_period))

    upper, ma, lower = bollinger(
        close,
        int(config.boll_period),
        float(config.boll_std_mult),
    )
    out["range_boll_upper"] = upper
    out["range_boll_ma"] = ma
    out["range_boll_lower"] = lower
    out["range_boll_width"] = upper - lower
    out["is_range_stable"] = _is_range_stable(out, config)
    return out


def _is_range_stable(
    frame: pd.DataFrame,
    config: RangeStructureConfig,
) -> pd.Series:
    """Return the full RSI/Bollinger/ATR sideways-range gate."""
    range_atr = frame["range_atr"]
    ready = frame[[
        "range_width",
        "range_atr",
        "range_boll_width",
        "range_rsi",
    ]].notna().all(axis=1)
    stable = (
        (frame["range_width"] > 0)
        & (frame["range_width"] <= float(config.width_atr_max) * range_atr)
        & (frame["range_boll_width"] <= float(config.boll_width_atr_max) * range_atr)
        & ((frame["range_rsi"] - float(config.rsi_mid)).abs() <= float(config.rsi_band))
    )
    return stable.where(ready)
