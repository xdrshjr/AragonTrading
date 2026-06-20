"""OKX 归一化单测 (spec R7 / C2)：毫秒->秒、升序、number 类型。"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.okx.rest_client import OKXRestClient
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


class _StubRest(OKXRestClient):
    """覆盖 _request 喂入固定 tickers 行，验证 get_instruments 解析/排序（不联网）。"""

    def __init__(self, rows: list[dict]) -> None:
        super().__init__()
        self._rows = rows

    async def _request(self, path: str, params: dict, *, retries: int = 3) -> list:
        assert path == "/api/v5/market/tickers"
        return self._rows


async def test_get_instruments_parses_quotes_and_sorts_by_turnover():
    rows = [
        {"instId": "AAVE-USDT-SWAP", "last": "100", "open24h": "80",
         "vol24h": "10", "volCcy24h": "1000"},
        {"instId": "BTC-USDT-SWAP", "last": "64000", "open24h": "0",
         "vol24h": "5", "volCcy24h": "9000000"},
        {"instId": "ETH-USDT-SWAP", "last": "3000", "open24h": "3300",
         "vol24h": "20", "volCcy24h": "5000000"},
        {"instId": "BADROW"},  # 无法切分 base/quote -> 跳过
    ]
    out = await _StubRest(rows).get_instruments("SWAP")
    # 按 volCcy24h 降序：BTC(9e6) > ETH(5e6) > AAVE(1e3)，无法切分的行被丢弃
    assert [i.instId for i in out] == ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "AAVE-USDT-SWAP"]
    aave = out[-1]
    assert aave.base == "AAVE" and aave.quote == "USDT"
    assert aave.last == 100 and aave.open24h == 80
    assert aave.vol24h == 10 and aave.volCcy24h == 1000
    assert aave.changePct == pytest.approx((100 - 80) / 80)
    # open24h=0 -> changePct None（不除零）
    assert out[0].open24h == 0 and out[0].changePct is None
    # 负向涨跌正确
    assert out[1].changePct == pytest.approx((3000 - 3300) / 3300)


async def test_get_instruments_tolerates_missing_quote_fields():
    out = await _StubRest([{"instId": "OP-USDT", "last": "", "volCcy24h": ""}]).get_instruments("SPOT")
    assert len(out) == 1
    inst = out[0]
    assert inst.base == "OP" and inst.quote == "USDT"
    assert inst.last is None and inst.volCcy24h is None and inst.changePct is None


async def test_instruments_endpoint_rejects_bad_inst_type():
    from app.db.session import dispose_engine, init_db
    from app.main import app

    await init_db()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/instruments", params={"instType": "OPTION"})
            assert r.status_code == 400
            assert r.json()["error"]["code"] == "BAD_INST_TYPE"
    finally:
        await dispose_engine()
