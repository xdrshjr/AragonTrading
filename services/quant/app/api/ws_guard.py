"""WebSocket 侧的来源守卫（trust-boundary-and-signal-integrity D1）。

**WS 完全不过 CORS**：`starlette/middleware/cors.py:76` 对非 http scope 直接放行，所以就算把
`allow_origins` 收紧到白名单，9 条 `@router.websocket` 路由仍然对任意 `Origin` 敞开——而 WS 的响应
是**可读**的，任意网页里一个 sandboxed iframe 就能把用户的实盘会话、成交、持仓订阅走。
**修 CORS 而不修 WS，等于只锁了前门。**

调用约定：每条 WS 路由的**第一行**是 `if not await guard_ws_origin(websocket): return`，
必须排在 `websocket.accept()` **之前**——不合规连接一帧都不许收到（哪怕一次快照）。
"""
from __future__ import annotations

import logging

from fastapi import WebSocket

from ..config import get_settings
from ..security.origins import is_allowed_origin

logger = logging.getLogger("aragon.security")

WS_ORIGIN_NOT_ALLOWED_CODE = 1008   # RFC 6455 policy violation


async def guard_ws_origin(websocket: WebSocket) -> bool:
    """握手期来源判定；不合规即 `close(1008)` 并返回 ``False``。

    Args:
        websocket: 尚未 `accept()` 的连接。

    Returns:
        ``True`` 表示放行（调用方继续 `accept()`）；``False`` 表示**已经关闭**，调用方必须直接 `return`。
    """
    origin = websocket.headers.get("origin")
    if is_allowed_origin(origin, get_settings().allowed_origin_list()):
        return True
    logger.warning("ws origin rejected: %r on %s", origin, websocket.scope.get("path", "?"))
    await websocket.close(code=WS_ORIGIN_NOT_ALLOWED_CODE)
    return False
