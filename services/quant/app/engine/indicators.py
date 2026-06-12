"""技术指标 —— 纯函数，自实现以规避 pandas-ta / numpy 2.x 兼容问题 (spec §6 C9)。

输入统一为含 (o,h,l,c,vol,ts) 列的 pd.DataFrame；输出为新列（不就地修改入参）。
VWAP 按 UTC 自然日重置 (C5)。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..okx.types import INTRADAY_BARS


def ema(series: pd.Series, period: int) -> pd.Series:
    """指数移动平均。"""
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """简单移动平均。"""
    return series.rolling(window=period, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI（用 EMA 平滑的经典实现）。"""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss == 0 -> RSI = 100
    out = out.where(avg_loss != 0, 100.0)
    return out


def bollinger(series: pd.Series, period: int = 20, std_mult: float = 2.0
              ) -> tuple[pd.Series, pd.Series, pd.Series]:
    """布林带，返回 (upper, ma, lower)。"""
    ma = series.rolling(window=period, min_periods=period).mean()
    std = series.rolling(window=period, min_periods=period).std(ddof=0)
    upper = ma + std_mult * std
    lower = ma - std_mult * std
    return upper, ma, lower


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """平均真实波幅（Wilder 平滑）。"""
    high, low, close = df["h"], df["l"], df["c"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def vwap_session(df: pd.DataFrame, bar: str) -> pd.Series:
    """按 UTC 自然日重置的 VWAP (C5)。

    typical_price = (h+l+c)/3；每个 UTC 自然日（ts // 86400）内累计。
    非日内周期（bar ≥ 1D）返回全 NaN。
    """
    if bar not in INTRADAY_BARS:
        return pd.Series(np.nan, index=df.index)
    typical = (df["h"] + df["l"] + df["c"]) / 3.0
    pv = typical * df["vol"]
    day = (df["ts"] // 86400).astype("int64")
    cum_pv = pv.groupby(day).cumsum()
    cum_vol = df["vol"].groupby(day).cumsum()
    return cum_pv / cum_vol.replace(0.0, np.nan)
