"""OKX 原始 / 归一化数据模型 (spec §7.2 / §9.3)。

归一化后的 Candle.ts 一律为「秒级 UTC 整数」(C2)。
"""
from __future__ import annotations

from pydantic import BaseModel


class Candle(BaseModel):
    """归一化 K 线（升序、秒级 UTC ts、number 类型）。"""

    ts: int       # 秒级 UTC
    o: float
    h: float
    l: float
    c: float
    vol: float
    confirm: bool = True


class Instrument(BaseModel):
    """可交易标的。"""

    instId: str
    instType: str  # "SWAP" | "SPOT"
    base: str
    quote: str


# OKX candles 行的列顺序 (spec §7.2):
# [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
OKX_CANDLE_COLUMNS = ["ts", "o", "h", "l", "c", "vol", "volCcy", "volCcyQuote", "confirm"]

# bar -> OKX WS channel
BAR_TO_WS_CHANNEL = {
    "1m": "candle1m",
    "5m": "candle5m",
    "15m": "candle15m",
    "30m": "candle30m",
    "1H": "candle1H",
    "4H": "candle4H",
    "1D": "candle1D",
}

# 每个 bar 的秒数（用于 VWAP 锚定 / 增量翻页 / 年化）
BAR_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1H": 3600,
    "4H": 14400,
    "1D": 86400,
}

# 日内周期集合（VWAP 仅在日内有效，C5）
INTRADAY_BARS = {"1m", "5m", "15m", "30m", "1H"}


def parse_okx_candle(row: list[str]) -> Candle:
    """把 OKX 单行原始字符串数组归一化为 Candle。

    OKX ts 为毫秒字符串；此处 ÷1000 转为秒（唯一转换边界，C2）。
    """
    ms = int(row[0])
    return Candle(
        ts=ms // 1000,
        o=float(row[1]),
        h=float(row[2]),
        l=float(row[3]),
        c=float(row[4]),
        vol=float(row[5]),
        confirm=(str(row[8]) == "1") if len(row) > 8 else True,
    )
