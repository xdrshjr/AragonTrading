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
from ..okx.ws_supervisor import StreamEvent
from .ws_guard import guard_ws_origin

router = APIRouter(tags=["ws"])

INST_ID_RE = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+(?:-[A-Z0-9]+)?$")


@router.websocket("/ws/market")
async def ws_market(websocket: WebSocket) -> None:
    # 来源守卫**不产生任何帧**，因此不与「本路由 idle 时必须绝对静默」那条约束冲突
    # （resilience-and-hci-hardening 硬约束 2：加保活会把客户端 45s 停摆看门狗致盲）。
    if not await guard_ws_origin(websocket):   # 必须在 accept() 之前：不合规连接一帧都不许收到
        return
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

    async def on_stream(event: StreamEvent) -> None:
        # 链路状态是**加法**事件类型。该前提在引入本帧时并不成立——彼时前端用 else 兜底把未
        # 匹配的帧一律当 K 线，本帧遂以 ts=undefined 进图表数据打崩渲染层致桌面端白屏
        # （docs/diagnoses/desktop-white-screen-market-ws-link-frame）。现前端改为白名单路由
        # （web/lib/marketFrames.routeMarketFrame），再加新事件类型前请确认它仍是白名单。
        # 现状：本帧**尚无前端消费者**（顶栏链路芯片走 REST /api/okx/connectivity 轮询）。
        # 保留它是为了让「浏览器无需等 15s 轮询即可如实显示正在重连」这条路径可用；若决定不做，
        # 应连同前端的 link 分支一起删掉，而不是留着一条没人读的流。
        await _send_if_open(closed, websocket, "link", {
            "key": event.key, "state": event.state, "attempt": event.attempt,
            "downtimeS": round(event.downtime_s, 1), "detail": event.detail})

    client = OKXMarketWSClient(
        inst_id,
        bar,
        MarketWSHandlers(
            on_candle=on_candle,
            on_trade=on_trade,
            on_ticker=on_ticker,
            on_book=on_book,
            on_error=on_error,
            on_stream=on_stream,
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
