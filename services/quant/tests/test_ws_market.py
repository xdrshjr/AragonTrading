from __future__ import annotations

import asyncio

import pytest
from starlette.websockets import WebSocketDisconnect

from app.api.ws_market import ws_market
from app.okx.types import Candle, MarketTicker, MarketTrade, OrderBookLevel, OrderBookSnapshot
from app.okx.ws_client import MarketWSHandlers, OKXMarketWSClient, OKXWSClient


class _FakeMarketClient:
    created: list["_FakeMarketClient"] = []

    def __init__(self, inst_id: str, bar: str, handlers, settings=None, market_channels: bool = True):
        self.inst_id = inst_id
        self.bar = bar
        self.handlers = handlers
        self.market_channels = market_channels
        self.started = False
        self.stopped = False
        self.task: asyncio.Task | None = None
        _FakeMarketClient.created.append(self)

    def start(self) -> None:
        self.started = True
        self.task = asyncio.create_task(self._emit())

    async def stop(self) -> None:
        self.stopped = True
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def _emit(self) -> None:
        await self.handlers.on_candle(
            Candle(ts=1710000000, o=1, h=2, l=0.5, c=1.5, vol=10, confirm=False)
        )
        await self.handlers.on_trade(
            MarketTrade(ts=1710000001, price=1.6, qty=0.2, side="buy", trade_id="t1")
        )
        await self.handlers.on_ticker(
            MarketTicker(
                ts=1710000002,
                last=1.7,
                open24h=1.1,
                vol24h=100,
                vol_ccy24h=170,
            )
        )
        await self.handlers.on_book(
            OrderBookSnapshot(
                ts=1710000003,
                bids=[OrderBookLevel(price=1.6, qty=2, orders=4)],
                asks=[OrderBookLevel(price=1.8, qty=3, orders=5)],
            )
        )


class _FakeWebSocket:
    def __init__(self, query_params, expect: int = 4):
        self.query_params = query_params
        self.expect = expect
        self.accepted = False
        self.closed = False
        self.sent = []

    async def accept(self):
        self.accepted = True

    async def close(self):
        self.closed = True

    async def send_text(self, text):
        import json
        self.sent.append(json.loads(text))

    async def receive_text(self):
        while len(self.sent) < self.expect and not self.closed:
            await asyncio.sleep(0)
        raise WebSocketDisconnect()


class _ErrorThenCandleClient:
    """Upstream that errors on a non-candle channel, then keeps streaming candles."""

    last: "_ErrorThenCandleClient | None" = None

    def __init__(self, inst_id: str, bar: str, handlers, settings=None, market_channels: bool = True):
        self.handlers = handlers
        self.market_channels = market_channels
        self.task: asyncio.Task | None = None
        _ErrorThenCandleClient.last = self

    def start(self) -> None:
        self.task = asyncio.create_task(self._emit())

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def _emit(self) -> None:
        # OKX rejects a public-only channel on the business endpoint with 60018 ...
        await self.handlers.on_error("60018", "Wrong URL or channel:trades ... doesn't exist")
        # ... but the valid candle channel keeps streaming and must still arrive.
        await self.handlers.on_candle(
            Candle(ts=1710000000, o=1, h=2, l=0.5, c=1.5, vol=10, confirm=False)
        )


@pytest.mark.asyncio
async def test_ws_market_forwards_typed_market_events(monkeypatch):
    _FakeMarketClient.created = []
    monkeypatch.setattr("app.api.ws_market.OKXMarketWSClient", _FakeMarketClient)
    websocket = _FakeWebSocket({"instId": "BTC-USDT-SWAP", "bar": "1m"})
    await ws_market(websocket)
    candle, trade, ticker, book = websocket.sent

    fake = _FakeMarketClient.created[0]
    assert fake.inst_id == "BTC-USDT-SWAP"
    assert fake.bar == "1m"
    assert fake.started is True
    assert fake.stopped is True
    assert candle["type"] == "candle"
    assert trade == {
        "type": "trade",
        "data": {"ts": 1710000001, "price": 1.6, "qty": 0.2, "side": "buy", "tradeId": "t1"},
    }
    assert ticker == {
        "type": "ticker",
        "data": {
            "ts": 1710000002,
            "last": 1.7,
            "open24h": 1.1,
            "vol24h": 100.0,
            "volCcy24h": 170.0,
        },
    }
    assert book["type"] == "book"
    assert book["data"]["bids"][0] == {"price": 1.6, "qty": 2.0, "orders": 4}


@pytest.mark.asyncio
async def test_ws_market_rejects_invalid_params():
    websocket = _FakeWebSocket({"instId": "bad id", "bar": "2m"})
    await ws_market(websocket)
    assert websocket.sent[0]["type"] == "error"
    assert websocket.sent[0]["data"]["code"] == "INVALID_MARKET_SUBSCRIPTION"
    assert websocket.closed is True


@pytest.mark.asyncio
async def test_market_client_subscription_scope():
    async def on_candle(_candle):
        return None

    handlers = MarketWSHandlers(
        on_candle=on_candle,
        on_trade=lambda _trade: on_candle(_trade),
        on_ticker=lambda _ticker: on_candle(_ticker),
        on_book=lambda _book: on_candle(_book),
    )
    market = OKXMarketWSClient("BTC-USDT-SWAP", "1m", handlers)
    candle_only = OKXWSClient("BTC-USDT-SWAP", "1m", on_candle)

    assert [arg["channel"] for arg in market._subscription_args()] == [
        "candle1m",
        "trades",
        "tickers",
        "books5",
    ]
    assert candle_only._client._subscription_args() == [
        {"channel": "candle1m", "instId": "BTC-USDT-SWAP"}
    ]


@pytest.mark.asyncio
async def test_ws_market_subscribes_candle_only(monkeypatch):
    _FakeMarketClient.created = []
    monkeypatch.setattr("app.api.ws_market.OKXMarketWSClient", _FakeMarketClient)
    websocket = _FakeWebSocket({"instId": "BTC-USDT-SWAP", "bar": "1m"})
    await ws_market(websocket)

    fake = _FakeMarketClient.created[0]
    # The chart route must not push public-only channels to the business endpoint.
    assert fake.market_channels is False


@pytest.mark.asyncio
async def test_ws_market_keeps_candle_stream_after_non_candle_error(monkeypatch):
    _ErrorThenCandleClient.last = None
    monkeypatch.setattr("app.api.ws_market.OKXMarketWSClient", _ErrorThenCandleClient)
    websocket = _FakeWebSocket({"instId": "BTC-USDT-SWAP", "bar": "1m"}, expect=2)
    await ws_market(websocket)

    types = [message["type"] for message in websocket.sent]
    # The per-channel 60018 error is forwarded as advisory information ...
    assert "error" in types
    assert websocket.sent[0]["data"]["code"] == "60018"
    # ... but it must NOT tear down the shared candle stream: the candle that OKX
    # keeps sending still reaches the browser, and the socket stays open.
    assert "candle" in types
    assert websocket.closed is False
