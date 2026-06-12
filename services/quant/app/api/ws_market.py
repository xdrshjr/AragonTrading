"""路由：ws /ws/market —— 转发 OKX 实时 K 线给浏览器 (spec §9.2)。"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..okx.types import Candle
from ..okx.ws_client import OKXWSClient

router = APIRouter(tags=["ws"])


@router.websocket("/ws/market")
async def ws_market(websocket: WebSocket) -> None:
    await websocket.accept()
    inst_id = websocket.query_params.get("instId", "BTC-USDT-SWAP")
    bar = websocket.query_params.get("bar", "1H")

    loop = asyncio.get_event_loop()
    closed = asyncio.Event()

    async def on_candle(candle: Candle) -> None:
        if closed.is_set():
            return
        try:
            await websocket.send_text(json.dumps({"type": "candle", "data": {
                "ts": candle.ts, "o": candle.o, "h": candle.h, "l": candle.l,
                "c": candle.c, "vol": candle.vol, "confirm": candle.confirm}}))
        except Exception:  # noqa: BLE001
            closed.set()

    client = OKXWSClient(inst_id, bar, on_candle)
    client.start()
    try:
        # 维持连接：等待客户端关闭（或读取心跳）
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
