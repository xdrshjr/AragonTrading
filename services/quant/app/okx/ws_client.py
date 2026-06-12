"""OKX 公共 WebSocket 订阅 (spec §7.2 / §9.2)。

订阅 candle{bar} 频道，心跳保活 + 指数退避重连，把每根 K 线归一化为秒级 ts 后回调。
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import websockets

from ..config import Settings, get_settings
from .types import BAR_TO_WS_CHANNEL, Candle

logger = logging.getLogger("aragon.okx.ws")

CandleHandler = Callable[[Candle], Awaitable[None]]


class OKXWSClient:
    """单个 (instId, bar) 订阅的 OKX 公共 WS 客户端。"""

    def __init__(self, inst_id: str, bar: str, on_candle: CandleHandler,
                 settings: Settings | None = None):
        self._settings = settings or get_settings()
        self.inst_id = inst_id
        self.bar = bar
        self._on_candle = on_candle
        self._channel = BAR_TO_WS_CHANNEL.get(bar, "candle1H")
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _run(self) -> None:
        backoff = 1.0
        # 心跳由本类 _heartbeat 主动发 "ping" 文本帧维持，故关闭库内置 ping。
        # 注：websockets 14.x 的 connect() 无 proxy 形参（原生代理直到 15.0 才支持），
        # 因此 WS 依赖系统/TUN 代理（本机 Clash）连通 OKX；REST 侧已在 httpx 显式透传 proxy。
        connect_kwargs: dict = {"ping_interval": None, "open_timeout": 15}
        while not self._stop.is_set():
            try:
                async with websockets.connect(self._settings.okx_ws_public, **connect_kwargs) as ws:
                    await ws.send(json.dumps({
                        "op": "subscribe",
                        "args": [{"channel": self._channel, "instId": self.inst_id}],
                    }))
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
                logger.warning("OKX WS 断线，%.1fs 后重连: %s", backoff, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _heartbeat(self, ws) -> None:
        """OKX 要求 ~25s 内有数据往返，否则断开；主动发 'ping' 文本帧。"""
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
        if "event" in msg:  # subscribe ack / error
            if msg.get("event") == "error":
                logger.warning("OKX WS 订阅错误: %s", msg)
            return
        data = msg.get("data")
        if not data:
            return
        for row in data:
            # candle 行同 REST：[ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
            try:
                candle = Candle(
                    ts=int(row[0]) // 1000,
                    o=float(row[1]), h=float(row[2]), l=float(row[3]),
                    c=float(row[4]), vol=float(row[5]),
                    confirm=(str(row[8]) == "1") if len(row) > 8 else False,
                )
            except (ValueError, IndexError):
                continue
            await self._on_candle(candle)
