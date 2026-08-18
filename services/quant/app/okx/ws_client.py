"""OKX public WebSocket clients.

连接的**活性判据、退避与重连**已下沉到 ``okx/ws_supervisor.py``（okx-link-resilience §2.2）；
本模块保留对外 API（``OKXMarketWSClient`` / ``OKXWSClient`` 的构造与 ``start/stop`` 语义、
一个 endpoint 一个 ``asyncio.Task``）与消息解析分发，并**仍在本模块内**发起
``websockets.connect``——既有测试 monkeypatch 的正是 ``app.okx.ws_client.websockets.connect``。
"""
from __future__ import annotations

import asyncio
import inspect
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
from .ws_supervisor import StreamEvent, WSStreamConfig, WSSupervisor

logger = logging.getLogger("aragon.okx.ws")

CandleHandler = Callable[[Candle], Awaitable[None]]
TradeHandler = Callable[[MarketTrade], Awaitable[None]]
TickerHandler = Callable[[MarketTicker], Awaitable[None]]
BookHandler = Callable[[OrderBookSnapshot], Awaitable[None]]
ErrorHandler = Callable[[str, str], Awaitable[None]]
StreamHandler = Callable[[StreamEvent], Awaitable[None]]


async def _noop_error(_code: str, _message: str) -> None:
    return None


async def _noop_stream(_event: StreamEvent) -> None:
    return None


def _connect(url: str, **kwargs):
    """模块级建连接缝：**必须**留在本模块内，否则既有测试的 monkeypatch 目标失效（F-6）。

    返回值一律交给 ``async with``，**绝不 await**——真实 ``websockets.connect(...)`` 两者皆可，
    但既有替身 ``_FakeConnection`` 不可 await。
    """
    return websockets.connect(url, **kwargs)


def _connect_kwargs(settings: Settings) -> dict:
    """建连参数。代理是本轮最危险的一处，规则只有一条：**为空一律不传 `proxy` 键**。

    ``websockets`` 15 的 ``proxy`` **默认值是 ``True``**（走 ``urllib.request.getproxies()``，
    Windows 上**含注册表里的系统代理**）；传 ``None`` 是「**显式禁用**代理」而不是「不指定」。
    而 ``settings.proxy_url`` 只读 ``HTTPS_PROXY / HTTP_PROXY / ALL_PROXY`` 三个 env——本仓库的
    用户大量只配了系统代理（Clash Verge 等）而没有这三个 env，此时它恒为 ``None``。无条件传
    ``proxy=settings.proxy_url`` 会把这批用户的 WS **全部立刻断掉且永不恢复**，比现状严重得多。

    ``ping_interval=None`` 保留不动：应用层三重活性判据已覆盖存活，而改它会同时改变既有替身
    面对的参数面。库不支持 ``proxy`` 形参时（pin 的 14.1）也一律不传。
    """
    kwargs: dict = {"ping_interval": None, "open_timeout": 15}
    proxy = settings.proxy_url
    if proxy and "proxy" in inspect.signature(websockets.connect).parameters:
        kwargs["proxy"] = proxy
    return kwargs


@dataclass
class MarketWSHandlers:
    on_candle: CandleHandler
    on_trade: TradeHandler
    on_ticker: TickerHandler
    on_book: BookHandler
    on_error: ErrorHandler = _noop_error
    # 链路状态事件（加法字段，带默认值 ⇒ 既有构造点零改动）。
    on_stream: StreamHandler = _noop_stream


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
        owner: str = "chart",
    ):
        self._settings = settings or get_settings()
        self.inst_id = inst_id
        self.bar = bar
        self._handlers = handlers
        self._market_channels = market_channels
        self._owner = owner
        self._channel = BAR_TO_WS_CHANNEL.get(bar, "candle1H")
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    @property
    def is_alive(self) -> bool:
        """至少一条 endpoint 任务仍在跑。

        ``_run_endpoint`` 只吞 ``CancelledError``，supervisor 抛别的异常时任务会**静默死掉**
        ——这个属性就是那条静默的出口（ai-trade-lifecycle-truth B-4 / D-7）。

        读到「已 done 且带异常」的 task 时顺手取一次 ``task.exception()``：否则 asyncio 会在
        回收时打一条 "Task exception was never retrieved"，而那条噪音恰恰就是本属性想说出口
        的那件事，不该被丢在事件循环的日志里（[评审 R2-3]）。

        注意它**不是**「连接是否已建立」：连接的活性判据在 ``ws_supervisor`` 内部，重连期间
        task 仍在跑、本属性仍为真。它回答的只有一件事——「还有没有人在为这条流努力」。
        """
        if not self._tasks:
            return False
        alive = False
        for task in self._tasks:
            if not task.done():
                alive = True
                continue
            if not task.cancelled():
                exc = task.exception()
                if exc is not None:
                    logger.warning("OKX WS endpoint task died: %s: %s",
                                   type(exc).__name__, exc)
        return alive

    def start(self) -> None:
        if self._tasks:
            return
        self._tasks.append(asyncio.create_task(
            self._run_endpoint(self._stream_config("business"))
        ))
        if self._market_channels:
            self._tasks.append(asyncio.create_task(
                self._run_endpoint(self._stream_config("public"))
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

    def _stream_config(self, endpoint: str) -> WSStreamConfig:
        """一条 endpoint 的登记簿身份。

        通道键含 ``owner`` 段（``ws:{endpoint}:{instId}:{bar}:{owner}``）：不加这一段，一个
        live_auto 会话与一个浏览器图表订阅同一 ``BTC-USDT-SWAP:1H`` 时会**共写同一条记录**，
        一方断线另一方健康 → 面板状态来回翻，而两条连接的真实健康度谁也没被如实表达。
        """
        is_business = endpoint == "business"
        url = self._settings.okx_ws_business if is_business else self._settings.okx_ws_public
        label = (f"实时流(K 线 · {self.inst_id} {self.bar})" if is_business
                 else f"实时流(盘口 · {self.inst_id})")
        return WSStreamConfig(
            url=url,
            args=self._candle_args() if is_business else self._market_args(),
            kind="wsBusiness" if is_business else "wsPublic",
            label=label,
            key=f"ws:{endpoint}:{self.inst_id}:{self.bar}:{self._owner}",
        )

    async def _run_endpoint(self, cfg: WSStreamConfig) -> None:
        """一个 endpoint 一个 task（``_tasks`` 语义不变）；生命周期全权交给 supervisor。"""
        connect_kwargs = _connect_kwargs(self._settings)
        supervisor = WSSupervisor(
            cfg,
            settings=self._settings,
            connect_factory=lambda url: _connect(url, **connect_kwargs),
            on_raw=self._handle_message,
            on_stream=self._handlers.on_stream,
        )
        try:
            await supervisor.run(self._stop)
        except asyncio.CancelledError:
            return

    def _candle_args(self) -> list[dict[str, str]]:
        return [{"channel": self._channel, "instId": self.inst_id}]

    def _market_args(self) -> list[dict[str, str]]:
        return [
            {"channel": "trades", "instId": self.inst_id},
            {"channel": "tickers", "instId": self.inst_id},
            {"channel": "books5", "instId": self.inst_id},
        ]

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
        on_stream: StreamHandler | None = None,
        owner: str = "chart",
    ):
        self._client = OKXMarketWSClient(
            inst_id,
            bar,
            MarketWSHandlers(
                on_candle=on_candle,
                on_trade=_ignore_trade,
                on_ticker=_ignore_ticker,
                on_book=_ignore_book,
                on_stream=on_stream or _noop_stream,
            ),
            settings=settings,
            market_channels=False,
            owner=owner,
        )

    @property
    def is_alive(self) -> bool:
        """转发 ``OKXMarketWSClient.is_alive``（本类是它的薄包装）。"""
        return self._client.is_alive

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
