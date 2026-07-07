"""build_market_digest 单测（ai-trade-reasoning-chain §五 R1）。

覆盖：正常三段键齐全；禁用→None；窗口不足→None；全 0 成交量除零→该字段 None 而非抛错；
因果性（追加未来根再切回不改结果，RC-7 回归护栏）；因子读数与 engine/indicators 直算对拍
（RSI14/ATR14/BB20）；末行=当前决策根。纯离线（无网络、无 DB）。
"""
from __future__ import annotations

import pandas as pd

from app.ai_auto.market_digest import _MIN_BARS, build_market_digest
from app.config import Settings
from app.engine.indicators import atr, bollinger, rsi


def _make_df(n: int = 80, start: float = 30000.0, step: float = 20.0) -> pd.DataFrame:
    """严格升序的合成 OHLCV（末行=当前确认根），足够长以让 RSI14/BB20/EMA21/MACD 收敛。"""
    rows = []
    for i in range(n):
        c = start + i * step
        o = start + (i - 1) * step if i > 0 else c
        rows.append({"ts": 1_700_000_000 + i * 3600, "o": o, "h": max(o, c) + 5.0,
                     "l": min(o, c) - 5.0, "c": c, "vol": 100.0 + i})
    return pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "vol"])


def test_build_market_digest_returns_all_segments():
    digest = build_market_digest(_make_df(), "1H", Settings())
    assert digest is not None
    assert set(digest) == {"priceAction", "volume", "factors"}
    pa = digest["priceAction"]
    for key in ("changePct", "trend", "emaRelation", "emaSlopePct", "rangePos",
                "swingHigh", "swingLow", "consecUp", "consecDown", "lastBar"):
        assert key in pa
    for key in ("last", "sma", "ratio", "zscore", "trend", "spike"):
        assert key in digest["volume"]
    for key in ("rsi14", "macdHist", "bollPctB", "atrPct", "rocPct"):
        assert set(digest["factors"][key]) == {"value", "label"}


def test_rising_series_reads_as_uptrend():
    """严格升序 → trend=up、EMA 快线在上、连阳数 > 0（定性标签正确落定）。"""
    digest = build_market_digest(_make_df(), "1H", Settings())
    assert digest["priceAction"]["trend"] == "up"
    assert digest["priceAction"]["emaRelation"] == "fast_above"
    assert digest["priceAction"]["consecUp"] > 0


def test_disabled_returns_none():
    assert build_market_digest(_make_df(), "1H", Settings(ai_trade_market_digest=False)) is None


def test_insufficient_bars_returns_none():
    assert build_market_digest(_make_df(n=_MIN_BARS - 1), "1H", Settings()) is None


def test_none_df_returns_none():
    assert build_market_digest(None, "1H", Settings()) is None


def test_zero_volume_degrades_field_without_raising():
    """全 0 成交量致除零：ratio/zscore 返回 None（该字段降级），整段不抛错。"""
    df = _make_df()
    df["vol"] = 0.0
    digest = build_market_digest(df, "1H", Settings())
    assert digest is not None
    assert digest["volume"]["ratio"] is None
    assert digest["volume"]["zscore"] is None
    assert digest["volume"]["spike"] is False


def test_causality_future_bars_do_not_change_result():
    """RC-7 回归护栏：在 df.iloc[:k] 之后追加任意未来根，切回 :k 结果不变（无未来泄漏）。"""
    df = _make_df(n=80)
    k = 50
    base = build_market_digest(df.iloc[:k], "1H", Settings())
    future = _make_df(n=80, start=99000.0, step=-30.0)          # 走势相反的未来段
    extended = pd.concat([df.iloc[:k], future], ignore_index=True)
    assert build_market_digest(extended.iloc[:k], "1H", Settings()) == base


def test_factor_readings_match_engine_indicators():
    """因子读数与 engine/indicators 直算对拍（RSI14/ATR14/BB20 末值一致）。"""
    df = _make_df()
    close = df["c"].astype(float)
    factors = build_market_digest(df, "1H", Settings())["factors"]
    assert factors["rsi14"]["value"] == round(float(rsi(close, 14).iloc[-1]), 2)
    assert factors["atrPct"]["value"] == round(
        float(atr(df, 14).iloc[-1]) / float(close.iloc[-1]) * 100.0, 3)
    upper, _, lower = bollinger(close, 20, 2.0)
    span = float(upper.iloc[-1]) - float(lower.iloc[-1])
    expected_pctb = round((float(close.iloc[-1]) - float(lower.iloc[-1])) / span, 4)
    assert factors["bollPctB"]["value"] == expected_pctb


def test_last_bar_is_current_confirmed_bar():
    df = _make_df()
    digest = build_market_digest(df, "1H", Settings())
    assert digest["priceAction"]["lastBar"]["c"] == round(float(df["c"].iloc[-1]), 6)
