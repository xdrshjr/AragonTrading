"""R1 · 市场证据摘要（ai-trade-reasoning-chain §2.3）。

从当前基础周期窗口 `df`（`candles_to_df` 产物，列 ts/o/h/l/c/vol，升序，末行=当前确认根）**因果地**
（只用 ≤ 当前根的数据）算出三段紧凑证据，压成可注入决策 prompt 的 dict，让 AI 在「多策略立场」之外
再叠加「原始价格走势 / 成交量 / 经典技术因子读数」这层独立于所选策略的可解释证据——正合需求「综合价格
走势、成交量、各因子的情况一步步研判」之意。

**纯函数、无副作用、无网络、无 broker**，复用 `engine/indicators.py` 的 `ema/sma/rsi/bollinger/atr`
（自实现、已测、规避 pandas-ta 兼容问题）。**全程安全降级**：任一子字段分母为 0 / 数据不足 → 该字段 None；
禁用 / `len(df) < _MIN_BARS` / 计算异常 → 整段返回 None（prompt 省略该段），与 `market_context.
build_market_context` 同姿态——决策轮从不因证据计算崩溃或超时。

与「因子层」子系统的边界（§2.3 / §七 R-1）：此处「因子」取**通用技术因子读数**（RSI/MACD/BB/ATR/ROC），
**不接入**重型 `engine/factors/*`（挖掘/评分/权重模型）子系统。指标周期与阈值为模块级常量（规避「AI_TRADE_
env 三处联动」易踩点，与 `trace.py` 阈值常量同风格）。
"""
from __future__ import annotations

import math

import pandas as pd

from ..config import Settings
from ..engine.indicators import atr, bollinger, ema, rsi

# 最小窗口：够 RSI14 / BB20 / EMA21 及 MACD 慢线26+signal9 收敛（RC-6）。
_MIN_BARS = 35
# 指标周期（模块级常量，勿散落到 config 的 AI_TRADE_ env）。
_EMA_FAST, _EMA_SLOW = 9, 21
_EMA_SLOPE_LOOKBACK = 3          # 近 3 根 EMA 斜率（emaFast 与 iloc[-4] 比）
_RSI_PERIOD = 14
_MACD_FAST, _MACD_SLOW, _MACD_SIGNAL = 12, 26, 9
_BOLL_PERIOD, _BOLL_STD = 20, 2.0
_ATR_PERIOD = 14
_ROC_LOOKBACK = 10              # 动量 ROC 回看根数
_VOL_TREND_LOOKBACK = 5         # 量能趋势对比根数
_VOL_SPIKE_RATIO = 2.0          # 量比 ≥ 该值记异常放量
# 定性标签阈值（相对量纲、可后续调）。
_FLAT_CHANGE_PCT = 0.3          # |changePct| < 该值记 flat（趋势不明）
_VOL_TREND_PCT = 15.0           # 近 N 根量能变化 ±该值内记 flat
_ATR_HIGH_PCT, _ATR_LOW_PCT = 1.5, 0.5
_RSI_OVERBOUGHT, _RSI_OVERSOLD = 70.0, 30.0


def build_market_digest(df: pd.DataFrame, bar: str, settings: Settings) -> dict | None:
    """价格走势 + 成交量 + 技术因子的紧凑证据摘要；禁用 / 数据不足 / 异常一律 None（安全降级）。

    Args:
        df: 当前基础周期窗口（`candles_to_df` 产物，列 ts/o/h/l/c/vol，升序，末行=当前确认根）。
        bar: 标准 OKX 周期（保留形参对齐 `build_market_context`；当前判据全相对量纲、不分周期）。
        settings: 运行配置；`ai_trade_market_digest=False` 时直接返回 None（不注入）。

    Returns:
        `{priceAction, volume, factors}`——三段紧凑证据；禁用 / 窗口 < `_MIN_BARS` / 计算异常时
        返回 None（决策 prompt 省略该段，保持纯净）。各子字段分母为 0 / 数据不足时取 None（绝不假数据）。
    """
    if not settings.ai_trade_market_digest:
        return None
    if df is None or len(df) < _MIN_BARS:
        return None
    try:
        return {
            "priceAction": _price_action(df, settings),
            "volume": _volume(df, settings),
            "factors": _factors(df),
        }
    except Exception:  # noqa: BLE001 —— 任一子段异常不崩决策轮：整段省略（绝不假数据）
        return None


# ============================ 价格走势 ============================
def _price_action(df: pd.DataFrame, settings: Settings) -> dict:
    """价格走势证据：趋势 / 动量 / 均线结构 / 摆动结构 / 区间位置 / 连阳连阴 / 末根形态（因果）。"""
    close = df["c"].astype(float)
    n = min(int(settings.ai_trade_digest_trend_window), len(close) - 1)
    change_pct = round((float(close.iloc[-1]) / float(close.iloc[-1 - n]) - 1.0) * 100.0, 2)
    ema_struct = _ema_structure(close)
    return {
        "changePct": change_pct,
        "trend": _trend_label(change_pct),
        **ema_struct,
        **_range_structure(df, n),
        "consecUp": _consec_count(close, up=True),
        "consecDown": _consec_count(close, up=False),
        "lastBar": _last_bar(df),
    }


def _ema_structure(close: pd.Series) -> dict:
    """快慢 EMA 相对关系 + 近 3 根快线斜率（%）。"""
    ema_fast = ema(close, _EMA_FAST)
    ema_slow = ema(close, _EMA_SLOW)
    fast_last = float(ema_fast.iloc[-1])
    slow_last = float(ema_slow.iloc[-1])
    relation = "fast_above" if fast_last >= slow_last else "fast_below"
    slope_pct = None
    if len(ema_fast) > _EMA_SLOPE_LOOKBACK and fast_last != 0:
        prev = float(ema_fast.iloc[-1 - _EMA_SLOPE_LOOKBACK])
        slope_pct = round((fast_last - prev) / abs(fast_last) * 100.0, 3)
    return {"emaFast": fast_last, "emaSlow": slow_last,
            "emaRelation": relation, "emaSlopePct": slope_pct}


def _range_structure(df: pd.DataFrame, n: int) -> dict:
    """近 N 根区间位置（∈[0,1]，分母 0→None）+ 摆动高低点。"""
    high = df["h"].astype(float)
    low = df["l"].astype(float)
    close = df["c"].astype(float)
    swing_high = float(high.iloc[-n:].max())
    swing_low = float(low.iloc[-n:].min())
    span = swing_high - swing_low
    range_pos = round((float(close.iloc[-1]) - swing_low) / span, 4) if span > 0 else None
    return {"rangePos": range_pos, "swingHigh": swing_high,
            "swingLow": swing_low}


def _trend_label(change_pct: float) -> str:
    """趋势方向标签：|changePct| < 阈值记 flat，否则按符号定 up/down。"""
    if abs(change_pct) < _FLAT_CHANGE_PCT:
        return "flat"
    return "up" if change_pct > 0 else "down"


def _consec_count(close: pd.Series, *, up: bool) -> int:
    """末端连续阳（up=True）/ 阴（up=False）根数。"""
    count = 0
    for delta in reversed(close.diff().dropna().tolist()):
        if (up and delta > 0) or (not up and delta < 0):
            count += 1
        else:
            break
    return count


def _last_bar(df: pd.DataFrame) -> dict:
    """末根 OHLC + 实体占比（|c-o|/o×100；o=0→None）。"""
    row = df.iloc[-1]
    o, h, lo, c = float(row["o"]), float(row["h"]), float(row["l"]), float(row["c"])
    body_pct = round(abs(c - o) / o * 100.0, 3) if o else None
    return {"o": o, "h": h, "l": lo, "c": c, "bodyPct": body_pct}


# ============================ 成交量 ============================
def _volume(df: pd.DataFrame, settings: Settings) -> dict:
    """量能水平 / 量比 / zscore / 放缩趋势 / 异常放量（分母 0 或数据不足→该字段 None）。"""
    vol = df["vol"].astype(float)
    m = min(int(settings.ai_trade_digest_vol_window), len(vol))
    last = float(vol.iloc[-1])
    window = vol.iloc[-m:]
    mean = float(window.mean())
    std = float(window.std(ddof=0))
    ratio = round(last / mean, 3) if mean > 0 else None
    zscore = round((last - mean) / std, 3) if std > 0 else None
    return {"last": round(last, 4), "sma": round(mean, 4), "ratio": ratio,
            "zscore": zscore, "trend": _volume_trend(vol),
            "spike": bool(ratio is not None and ratio >= _VOL_SPIKE_RATIO)}


def _volume_trend(vol: pd.Series) -> str:
    """近 N 根量能方向：相对变化 > +阈值 rising / < −阈值 falling / 否则 flat。"""
    if len(vol) <= _VOL_TREND_LOOKBACK:
        return "flat"
    recent = float(vol.iloc[-1])
    past = float(vol.iloc[-1 - _VOL_TREND_LOOKBACK])
    if past <= 0:
        return "flat"
    change = (recent - past) / past * 100.0
    if change > _VOL_TREND_PCT:
        return "rising"
    if change < -_VOL_TREND_PCT:
        return "falling"
    return "flat"


# ============================ 技术因子 ============================
def _factors(df: pd.DataFrame) -> dict:
    """一组固定、经典技术因子读数（每项 {value, label}，供 prompt 直读、前端另做中文化）。"""
    close = df["c"].astype(float)
    return {
        "rsi14": _rsi_reading(close),
        "macdHist": _macd_reading(close),
        "bollPctB": _boll_reading(close),
        "atrPct": _atr_reading(df, close),
        "rocPct": _roc_reading(close),
    }


def _reading(value: float | None, label: str) -> dict:
    """统一读数信封：非有限数（NaN/Inf）归一为 None（绝不把脏数落进 prompt）。"""
    return {"value": value if value is not None and math.isfinite(value) else None,
            "label": label}


def _rsi_reading(close: pd.Series) -> dict:
    value = float(rsi(close, _RSI_PERIOD).iloc[-1])
    if value > _RSI_OVERBOUGHT:
        label = "overbought"
    elif value < _RSI_OVERSOLD:
        label = "oversold"
    else:
        label = "neutral"
    return _reading(round(value, 2), label)


def _macd_reading(close: pd.Series) -> dict:
    """MACD 柱（DIF−DEA 末值）：>0 bullish / <0 bearish。"""
    dif = ema(close, _MACD_FAST) - ema(close, _MACD_SLOW)
    hist = dif - ema(dif, _MACD_SIGNAL)
    last = float(hist.iloc[-1])
    label = "bullish" if last > 0 else "bearish"
    return _reading(last, label)


def _boll_reading(close: pd.Series) -> dict:
    """布林 %B：>1 upper_break / <0 lower_break / ≥0.5 upper_half / 否则 lower_half（带宽 0→None）。"""
    upper, _, lower = bollinger(close, _BOLL_PERIOD, _BOLL_STD)
    up = float(upper.iloc[-1])
    low = float(lower.iloc[-1])
    span = up - low
    if not math.isfinite(span) or span <= 0:
        return _reading(None, "neutral")
    pct_b = (float(close.iloc[-1]) - low) / span
    if pct_b > 1:
        label = "upper_break"
    elif pct_b < 0:
        label = "lower_break"
    elif pct_b >= 0.5:
        label = "upper_half"
    else:
        label = "lower_half"
    return _reading(round(pct_b, 4), label)


def _atr_reading(df: pd.DataFrame, close: pd.Series) -> dict:
    """ATR% = atr14/close×100：>阈 high / <阈 low / 否则 normal（close 0→None）。"""
    atr_val = float(atr(df, _ATR_PERIOD).iloc[-1])
    price = float(close.iloc[-1])
    atr_pct = round(atr_val / price * 100.0, 3) if price else None
    if atr_pct is None:
        label = "normal"
    elif atr_pct > _ATR_HIGH_PCT:
        label = "high"
    elif atr_pct < _ATR_LOW_PCT:
        label = "low"
    else:
        label = "normal"
    return _reading(atr_pct, label)


def _roc_reading(close: pd.Series) -> dict:
    """动量 ROC = (c/c[-1-10]-1)×100：>0 positive / <0 negative / 0 或不足 neutral。"""
    if len(close) <= _ROC_LOOKBACK:
        return _reading(None, "neutral")
    past = float(close.iloc[-1 - _ROC_LOOKBACK])
    roc = round((float(close.iloc[-1]) / past - 1.0) * 100.0, 3) if past else None
    if roc is None:
        label = "neutral"
    else:
        label = "positive" if roc > 0 else ("negative" if roc < 0 else "neutral")
    return _reading(roc, label)
