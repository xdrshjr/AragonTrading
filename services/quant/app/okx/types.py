"""OKX 原始 / 归一化数据模型 (spec §7.2 / §9.3)。

归一化后的 Candle.ts 一律为「秒级 UTC 整数」(C2)。
"""
from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("aragon.okx.types")


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
    """可交易标的（携带 24h 行情快照，供前端列表展示与成交额排序）。

    行情字段均为 `Optional[float]`、默认 `None`，向后兼容旧调用方；
    `volCcy24h` 仅作同一 `instType` 列表内的成交额排序启发式（跨类型口径不同，不可比）。
    """

    instId: str
    instType: str  # "SWAP" | "SPOT" | "FUTURES"
    base: str
    quote: str
    last: float | None = None        # 最新成交价
    open24h: float | None = None     # 24h 前价（算涨跌）
    vol24h: float | None = None      # 24h 成交量（基础币）
    volCcy24h: float | None = None   # 24h 成交额（计价币）→ 排序键
    changePct: float | None = None   # (last-open24h)/open24h


class MarketTrade(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ts: int
    price: float
    qty: float
    side: Literal["buy", "sell"]
    trade_id: str | None = Field(default=None, alias="tradeId")


class OrderBookLevel(BaseModel):
    price: float
    qty: float
    orders: int | None = None


class OrderBookSnapshot(BaseModel):
    ts: int
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]


class MarketTicker(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ts: int
    last: float
    open24h: float | None = None
    vol24h: float | None = None
    vol_ccy24h: float | None = Field(default=None, alias="volCcy24h")


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
    "12H": 43200,
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


def parse_okx_trade(row: dict) -> MarketTrade | None:
    try:
        side = row["side"]
        if side not in {"buy", "sell"}:
            return None
        return MarketTrade(
            ts=int(row["ts"]) // 1000,
            price=float(row["px"]),
            qty=float(row["sz"]),
            side=side,
            trade_id=row.get("tradeId"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.debug("drop malformed OKX trade row: %s (%s)", row, exc)
        return None


def parse_okx_ticker(row: dict) -> MarketTicker | None:
    try:
        return MarketTicker(
            ts=int(row["ts"]) // 1000,
            last=float(row["last"]),
            open24h=_optional_float(row.get("open24h")),
            vol24h=_optional_float(row.get("vol24h")),
            vol_ccy24h=_optional_float(row.get("volCcy24h")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.debug("drop malformed OKX ticker row: %s (%s)", row, exc)
        return None


def parse_okx_books5(row: dict) -> OrderBookSnapshot | None:
    try:
        asks = [_parse_book_level(level) for level in row.get("asks", [])]
        bids = [_parse_book_level(level) for level in row.get("bids", [])]
        if any(level is None for level in asks + bids):
            return None
        return OrderBookSnapshot(
            ts=int(row["ts"]) // 1000,
            asks=[level for level in asks if level is not None],
            bids=[level for level in bids if level is not None],
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.debug("drop malformed OKX books5 row: %s (%s)", row, exc)
        return None


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _parse_book_level(level: list[str]) -> OrderBookLevel | None:
    try:
        orders = int(level[3]) if len(level) > 3 and level[3] != "" else None
        return OrderBookLevel(price=float(level[0]), qty=float(level[1]), orders=orders)
    except (IndexError, TypeError, ValueError) as exc:
        logger.debug("drop malformed OKX book level: %s (%s)", level, exc)
        return None
