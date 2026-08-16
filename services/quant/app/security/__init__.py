"""本机 API 的信任边界（trust-boundary-and-signal-integrity D1）。

本包只做**纯判定**，不持有状态、不碰 DB、不碰 OKX：`origins` 是来源白名单的纯函数，
`origin_middleware` 是把它挂到 HTTP 请求上的纯 ASGI 中间件。WS 侧的挂载点在
`app/api/ws_guard.py`（WebSocket 完全不过 CORS，必须单独守）。
"""
from __future__ import annotations

from .origins import DEFAULT_ORIGINS, is_allowed_origin, normalize
from .origin_middleware import OriginGuardMiddleware

__all__ = ["DEFAULT_ORIGINS", "OriginGuardMiddleware", "is_allowed_origin", "normalize"]
