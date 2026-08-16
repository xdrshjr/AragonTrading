"""HTTP 侧的来源守卫 —— **纯 ASGI 中间件**（trust-boundary-and-signal-integrity §4.1 / P1-10）。

**为什么不是 `BaseHTTPMiddleware`**：后者会为每个请求起一个 anyio task group 把下游 app 包起来，
其已知故障模式是「客户端在响应产生前断开 → ``RuntimeError: No response returned``」。这个仓库此前
几乎碰不到它（浏览器 `fetch` 没有默认超时，请求只会一直等），而同一轮的 D3 做的正是**给所有请求装上
客户端主动 abort**——两条修法叠加会把那个故障模式从罕见变成「每一次超时都发生」。

纯 ASGI 形态只读一个 header、不碰 body、不构造 `Request`，因此没有 task group、没有 body 缓冲，
上述故障模式一条都不沾。
"""
from __future__ import annotations

import json
import logging

from .origins import is_allowed_origin

logger = logging.getLogger("aragon.security")

ORIGIN_NOT_ALLOWED_CODE = "ORIGIN_NOT_ALLOWED"
_ORIGIN_NOT_ALLOWED_MESSAGE = "请求来源未被允许（请检查 SECURITY_ALLOWED_ORIGINS）"


def _origin_header(scope: dict) -> str | None:
    """从 ASGI scope 的 raw headers 里取 ``Origin``（缺失返回 ``None``）。

    只比 ``b"origin"`` 一种拼写：ASGI 规范要求服务器把 header 名**一律小写**后放进 scope
    （uvicorn / hypercorn 都如此），所以这里不需要也不应该再做一次大小写折叠——多写一层
    `lower()` 会让人误以为这个字段可能是别的大小写，反而模糊了契约来自哪里。
    """
    for key, value in scope.get("headers") or ():
        if key == b"origin":
            return value.decode("latin-1")
    return None


class OriginGuardMiddleware:
    """跨源请求在**进入路由之前**就被 403 掉，不碰 DB、不碰 OKX、不下真实委托。

    这一层与 `CORSMiddleware` 分工不同，缺一不可：CORS 只让浏览器**读不到**响应，
    而请求早就执行完了；本中间件才是拦住「执行」的那层。
    """

    def __init__(self, app, allowed: list[str]) -> None:
        """
        Args:
            app: 下游 ASGI app（由 `Starlette.build_middleware_stack` 位置传入）。
            allowed: Origin 白名单，来自 ``Settings.allowed_origin_list()``。
        """
        self.app = app
        self.allowed = list(allowed)

    async def __call__(self, scope, receive, send) -> None:
        # 非 http scope（websocket / lifespan）原样放行：WS 由 `api/ws_guard.guard_ws_origin`
        # 在 `accept()` 之前守，在这里拦会连 lifespan 一起拦掉。
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        origin = _origin_header(scope)
        if is_allowed_origin(origin, self.allowed):
            await self.app(scope, receive, send)
            return
        logger.warning("origin rejected: %r on %s %s", origin,
                       scope.get("method"), scope.get("path"))
        await self._reject(send)

    @staticmethod
    async def _reject(send) -> None:
        """按仓库既有错误信封回 403（零新增 wire 形状）。"""
        body = json.dumps(
            {"error": {"code": ORIGIN_NOT_ALLOWED_CODE, "message": _ORIGIN_NOT_ALLOWED_MESSAGE}},
            ensure_ascii=False,
        ).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("latin-1")),
            ],
        })
        await send({"type": "http.response.body", "body": body})
