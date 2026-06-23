"""OKX public WebSocket clients."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import websockets

from ..config import Settings, get_settings
from .types import (
    BAR_TO_WS_CHANNEL,
    Candle,
    MarketTicker,
    MarketTrade,
    OrderBookSnapshot,
    parse_okx_books5,
    parse_okx_candle,
    parse_okx_ticker,
    parse_okx_trade,
)

logger = logging.getLogger("aragon.okx.ws")

CandleHandler = Callable[[Candle], Awaitable[None]]
TradeHandler = Callable[[MarketTrade], Awaitable[None]]
TickerHandler = Callable[[MarketTicker], Awaitable[None]]
BookHandler = Callable[[OrderBookSnapshot], Awaitable[None]]
ErrorHandler = Callable[[str, str], Awaitable[None]]


async def _noop_error(_code: str, _message: str) -> None:
    return None


@dataclass
class MarketWSHandlers:
    on_candle: CandleHandler
    on_trade: TradeHandler
    on_ticker: TickerHandler
    on_book: BookHandler
    on_error: ErrorHandler = _noop_error


class OKXMarketWSClient:
    """OKX market WS streams for the selected instrument and bar.

    OKX V5 serves the candle channels only on the *business* endpoint and
    trades/tickers/books5 only on the *public* endpoint, so a single socket
    cannot carry both. This client runs one connection loop per endpoint that
    needs to be live — always the business loop for the candle channel, and the
    public loop for the market channels when ``market_channels`` is true — and
    feeds both into the same handlers.
    """

    def __init__(
        self,
        inst_id: str,
        bar: str,
        handlers: MarketWSHandlers,
        settings: Settings | None = None,
        market_channels: bool = True,
    ):
        self._settings = settings or get_settings()
        self.inst_id = inst_id
        self.bar = bar
        self._handlers = handlers
        self._market_channels = market_channels
        self._channel = BAR_TO_WS_CHANNEL.get(bar, "candle1H")
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        if self._tasks:
            return
        self._tasks.append(asyncio.create_task(
            self._run_endpoint(self._settings.okx_ws_business, self._candle_args())
        ))
        if self._market_channels:
            self._tasks.append(asyncio.create_task(
                self._run_endpoint(self._settings.okx_ws_public, self._market_args())
            ))

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks = []

    async def _run_endpoint(self, url: str, args: list[dict[str, str]]) -> None:
        backoff = 1.0
        connect_kwargs: dict = {"ping_interval": None, "open_timeout": 15}
        while not self._stop.is_set():
            try:
                async with websockets.connect(url, **connect_kwargs) as ws:
                    await ws.send(json.dumps({"op": "subscribe", "args": args}))
                    backoff = 1.0
                    heartbeat = asyncio.create_task(self._heartbeat(ws))
                    try:
                        async for raw in ws:
                            await self._handle_message(raw)
                    finally:
                        heartbeat.cancel()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                if self._stop.is_set():
                    break
                logger.warning("OKX WS disconnected; reconnecting in %.1fs: %s", backoff, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    def _candle_args(self) -> list[dict[str, str]]:
        return [{"channel": self._channel, "instId": self.inst_id}]

    def _market_args(self) -> list[dict[str, str]]:
        return [
            {"channel": "trades", "instId": self.inst_id},
            {"channel": "tickers", "instId": self.inst_id},
            {"channel": "books5", "instId": self.inst_id},
        ]

    async def _heartbeat(self, ws) -> None:
        try:
            while True:
                await asyncio.sleep(20)
                await ws.send("ping")
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            return

    async def _handle_message(self, raw: str) -> None:
        if raw == "pong":
            return
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if "event" in msg:
            if msg.get("event") == "error":
                logger.warning("OKX WS subscription error: %s", msg)
                await self._handlers.on_error(
                    str(msg.get("code") or "OKX_WS_ERROR"),
                    str(msg.get("msg") or "OKX websocket error"),
                )
            return
        data = msg.get("data")
        if not data:
            return
        channel = (msg.get("arg") or {}).get("channel")
        for row in data:
            await self._dispatch_row(channel, row)

    async def _dispatch_row(self, channel: str | None, row) -> None:
        if channel == self._channel:
            try:
                candle = parse_okx_candle(row)
            except (ValueError, IndexError):
                return
            await self._handlers.on_candle(candle)
            return
        if channel == "trades":
            trade = parse_okx_trade(row)
            if trade is not None:
                await self._handlers.on_trade(trade)
            return
        if channel == "tickers":
            ticker = parse_okx_ticker(row)
            if ticker is not None:
                await self._handlers.on_ticker(ticker)
            return
        if channel == "books5":
            book = parse_okx_books5(row)
            if book is not None:
                await self._handlers.on_book(book)


class OKXWSClient:
    """Candle-only compatibility wrapper used by paper trading."""

    def __init__(
        self,
        inst_id: str,
        bar: str,
        on_candle: CandleHandler,
        settings: Settings | None = None,
    ):
        self._client = OKXMarketWSClient(
            inst_id,
            bar,
            MarketWSHandlers(
                on_candle=on_candle,
                on_trade=_ignore_trade,
                on_ticker=_ignore_ticker,
                on_book=_ignore_book,
            ),
            settings=settings,
            market_channels=False,
        )

    def start(self) -> None:
        self._client.start()

    async def stop(self) -> None:
        await self._client.stop()


async def _ignore_trade(_trade: MarketTrade) -> None:
    return None


async def _ignore_ticker(_ticker: MarketTicker) -> None:
    return None


async def _ignore_book(_book: OrderBookSnapshot) -> None:
    return None
