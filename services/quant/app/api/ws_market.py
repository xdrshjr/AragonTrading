"""Browser websocket route for live OKX market data."""
from __future__ import annotations

import asyncio
import json
import re

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..okx.types import (
    BAR_TO_WS_CHANNEL,
    Candle,
    MarketTicker,
    MarketTrade,
    OrderBookSnapshot,
)
from ..okx.ws_client import MarketWSHandlers, OKXMarketWSClient

router = APIRouter(tags=["ws"])

INST_ID_RE = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+(?:-[A-Z0-9]+)?$")


@router.websocket("/ws/market")
async def ws_market(websocket: WebSocket) -> None:
    await websocket.accept()
    inst_id = websocket.query_params.get("instId", "BTC-USDT-SWAP")
    bar = websocket.query_params.get("bar", "1H")
    if bar not in BAR_TO_WS_CHANNEL or not INST_ID_RE.match(inst_id):
        await _send_event(
            websocket,
            "error",
            {
                "code": "INVALID_MARKET_SUBSCRIPTION",
                "message": "Unsupported OKX instrument or bar.",
            },
        )
        await websocket.close()
        return

    closed = asyncio.Event()

    async def on_candle(candle: Candle) -> None:
        await _send_if_open(closed, websocket, "candle", candle.model_dump())

    async def on_trade(trade: MarketTrade) -> None:
        await _send_if_open(closed, websocket, "trade", trade.model_dump(by_alias=True))

    async def on_ticker(ticker: MarketTicker) -> None:
        await _send_if_open(closed, websocket, "ticker", ticker.model_dump(by_alias=True))

    async def on_book(book: OrderBookSnapshot) -> None:
        await _send_if_open(closed, websocket, "book", book.model_dump(by_alias=True))

    async def on_error(code: str, message: str) -> None:
        # A per-channel OKX error (e.g. 60018 "channel doesn't exist") is advisory:
        # forward it so the UI can flag that channel as unavailable, but never tear
        # down the shared candle stream. Transport-level failures are handled by the
        # client's own reconnect loop, not here.
        await _send_if_open(closed, websocket, "error", {"code": code, "message": message})

    client = OKXMarketWSClient(
        inst_id,
        bar,
        MarketWSHandlers(
            on_candle=on_candle,
            on_trade=on_trade,
            on_ticker=on_ticker,
            on_book=on_book,
            on_error=on_error,
        ),
        # market_channels defaults to True: the candle channel streams from the
        # business endpoint and trades/tickers/books5 from the public endpoint,
        # so the depth panel (asks/bids/trade tape/24h vol/spread) gets data.
    )
    client.start()
    try:
        while not closed.is_set():
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                continue
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        closed.set()
        await client.stop()


async def _send_if_open(
    closed: asyncio.Event,
    websocket: WebSocket,
    event_type: str,
    data,
) -> None:
    if closed.is_set():
        return
    try:
        await _send_event(websocket, event_type, data)
    except Exception:  # noqa: BLE001
        closed.set()


async def _send_event(websocket: WebSocket, event_type: str, data) -> None:
    await websocket.send_text(json.dumps({"type": event_type, "data": data}))
