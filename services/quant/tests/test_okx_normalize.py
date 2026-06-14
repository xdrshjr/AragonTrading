"""OKX 归一化单测 (spec R7 / C2)：毫秒->秒、升序、number 类型。"""
from __future__ import annotations

from app.okx.types import (
    parse_okx_books5,
    parse_okx_candle,
    parse_okx_ticker,
    parse_okx_trade,
)


def test_ms_to_seconds():
    # OKX 行：[ts(ms), o, h, l, c, vol, volCcy, volCcyQuote, confirm]
    row = ["1781287200000", "63836.3", "63907.2", "63681.5", "63799.9",
           "69400.64", "694.0", "44281773.95", "1"]
    c = parse_okx_candle(row)
    assert c.ts == 1781287200          # 毫秒 // 1000 = 秒
    assert isinstance(c.ts, int)
    assert c.o == 63836.3 and c.c == 63799.9
    assert c.confirm is True


def test_unconfirmed_flag():
    row = ["1781283600000", "1", "2", "0.5", "1.5", "10", "1", "1", "0"]
    c = parse_okx_candle(row)
    assert c.confirm is False


def test_parse_okx_trade_ms_to_seconds():
    row = {
        "instId": "BTC-USDT-SWAP",
        "tradeId": "744876980",
        "px": "65321.4",
        "sz": "0.03",
        "side": "buy",
        "ts": "1710000001123",
    }
    trade = parse_okx_trade(row)
    assert trade is not None
    assert trade.ts == 1710000001
    assert trade.price == 65321.4
    assert trade.qty == 0.03
    assert trade.side == "buy"
    assert trade.trade_id == "744876980"


def test_parse_okx_ticker_optional_fields():
    row = {
        "instId": "BTC-USDT-SWAP",
        "last": "65123.4",
        "open24h": "64000",
        "vol24h": "1234.5",
        "volCcy24h": "80000000",
        "ts": "1710000001999",
    }
    ticker = parse_okx_ticker(row)
    assert ticker is not None
    assert ticker.ts == 1710000001
    assert ticker.last == 65123.4
    assert ticker.open24h == 64000
    assert ticker.vol24h == 1234.5
    assert ticker.vol_ccy24h == 80000000


def test_parse_okx_books5_levels_and_orders():
    row = {
        "asks": [["65124.0", "1.2", "0", "3"]],
        "bids": [["65123.3", "2.4", "0", "5"]],
        "ts": "1710000002123",
    }
    book = parse_okx_books5(row)
    assert book is not None
    assert book.ts == 1710000002
    assert book.asks[0].price == 65124.0
    assert book.asks[0].qty == 1.2
    assert book.asks[0].orders == 3
    assert book.bids[0].price == 65123.3
    assert book.bids[0].qty == 2.4
    assert book.bids[0].orders == 5


def test_market_parsers_drop_malformed_rows():
    assert parse_okx_trade({"px": "1", "sz": "1", "side": "hold", "ts": "1"}) is None
    assert parse_okx_ticker({"last": "", "ts": "1"}) is None
    assert parse_okx_books5({"asks": [["bad"]], "bids": [], "ts": "1"}) is None
