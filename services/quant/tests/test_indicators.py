"""指标单测 (spec §13.1)。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.engine.indicators import atr, bollinger, ema, rsi, vwap_session


def _df(closes, highs=None, lows=None, vols=None, start_ts=1_700_000_000, step=3600):
    n = len(closes)
    return pd.DataFrame({
        "ts": [start_ts + i * step for i in range(n)],
        "o": closes,
        "h": highs or [c + 1 for c in closes],
        "l": lows or [c - 1 for c in closes],
        "c": closes,
        "vol": vols or [100.0] * n,
    })


def test_ema_matches_manual():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = ema(s, 3)
    # EMA(span=3, adjust=False): alpha = 2/(3+1)=0.5
    expected = [1.0, 1.5, 2.25, 3.125, 4.0625]
    assert np.allclose(out.values, expected)


def test_rsi_all_up_is_100():
    s = pd.Series([float(i) for i in range(1, 30)])
    out = rsi(s, 14)
    assert out.iloc[-1] == 100.0


def test_rsi_bounds():
    s = pd.Series(np.sin(np.linspace(0, 20, 100)) * 10 + 100)
    out = rsi(s, 14).dropna()
    assert (out >= 0).all() and (out <= 100).all()


def test_bollinger_mid_is_sma():
    closes = [float(i) for i in range(50)]
    df = _df(closes)
    upper, ma, lower = bollinger(df["c"], 20, 2.0)
    assert np.isclose(ma.iloc[-1], np.mean(closes[-20:]))
    mask = ma.notna()
    assert (upper[mask] >= ma[mask]).all()
    assert (lower[mask] <= ma[mask]).all()


def test_atr_positive():
    closes = list(np.cumsum(np.random.RandomState(1).randn(60)) + 100)
    df = _df(closes, highs=[c + 2 for c in closes], lows=[c - 2 for c in closes])
    out = atr(df, 14).dropna()
    assert (out > 0).all()


def test_vwap_resets_each_utc_day():
    # 两个 UTC 自然日，每日 24 根 1H
    closes = [100.0] * 48
    df = _df(closes, start_ts=0, step=3600)  # ts=0 是 1970-01-01 00:00 UTC
    v = vwap_session(df, "1H")
    # 第二天第一根（index 24, ts=86400）VWAP 应等于该根 typical price（重置）
    typical_24 = (df["h"].iat[24] + df["l"].iat[24] + df["c"].iat[24]) / 3
    assert np.isclose(v.iat[24], typical_24)


def test_vwap_nan_for_daily_bar():
    df = _df([100.0] * 10)
    v = vwap_session(df, "1D")
    assert v.isna().all()
