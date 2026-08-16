"""来源（Origin）白名单判定 —— 纯函数，无状态、无 I/O（trust-boundary-and-signal-integrity §4.1）。

**威胁模型**：攻击者是用户浏览器里的**任意网页**（广告位、被投毒的第三方站点）。本机已被植入原生
恶意软件不在模型内——那种情况下它可以直接读 `%APPDATA%/aragon-desktop/aragon.db`，应用层再设防
也无意义。判据因此很干净：浏览器发起的跨源 `fetch`/XHR（所有非 GET/HEAD 请求）与 WS 握手**必带**
`Origin` 头，所以「`Origin` 缺失」可靠地等价于「不是浏览器」。

两条容易被绕过的直觉必须点破：

1. **CORS 保护的是「读」，不是「执行」。** 浏览器只在拿不到 `ACAO` 时隐藏响应，请求本身早已到达
   服务端并被执行。所以拦住「执行」的必须是 `OriginGuardMiddleware` 这一层，CORS 只负责让浏览器
   同时也读不到。
2. **WebSocket 完全不过 CORS**（`starlette/middleware/cors.py:76` 对非 http scope 直接放行），
   所以 9 条 WS 路由必须各自调 `app/api/ws_guard.guard_ws_origin`。
"""
from __future__ import annotations

from urllib.parse import urlsplit

# 端口同步点第 6 处（见 desktop/README.md §五「端口同步点」）：18761 = Next 前端端口
# （`apps/web/package.json` 的 `next dev -p 18761`）。改端口必须同步这里，否则打开应用即「点什么都 403」。
DEFAULT_ORIGINS: tuple[str, ...] = (
    "http://localhost:18761",
    "http://127.0.0.1:18761",
)

# 不透明源（opaque origin）。带 `sandbox` 且不含 `allow-same-origin` 的 iframe 发的就是它，
# 而这个 iframe 可以由任意网页当场创建 —— 放行它等于把整条边界让给三行 HTML。
OPAQUE_ORIGIN = "null"


def normalize(origin: str) -> str:
    """归一化一个 Origin：小写 scheme + host、去掉尾斜杠、**保留端口**。

    Args:
        origin: 原始 Origin 字符串，如 ``"HTTP://LocalHost:18761/"``。

    Returns:
        归一化后的 ``scheme://host[:port]``；无法解析时返回去空白 + 小写的原串
        （它必然匹配不上白名单，因此「解析不了」天然等价于「不放行」）。
    """
    raw = (origin or "").strip().rstrip("/")
    if not raw:
        return ""
    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        return raw.lower()
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def is_allowed_origin(origin: str | None, allowed: list[str]) -> bool:
    """判定一个请求的 ``Origin`` 是否被放行。

    判据是**两态收敛**的（见 spec §三 D1 的判定表）：

    ========== ====== ================================================================
    ``Origin``  判定    理由
    ========== ====== ================================================================
    缺失/空     放行    非浏览器：浏览器的非 GET/HEAD `fetch`/XHR 与 WS 握手必带 Origin
    ``"null"``  拒绝    它是**浏览器**发的不透明源，而威胁模型正是浏览器
    在白名单     放行    —
    其余        拒绝    —
    ========== ====== ================================================================

    Args:
        origin: 请求头里的 ``Origin``（缺失时传 ``None``）。
        allowed: 白名单，来自 ``Settings.allowed_origin_list()``（已保证不含 ``*`` / ``null``）。

    Returns:
        ``True`` 表示放行。
    """
    if origin is None or not origin.strip():
        return True
    if origin.strip().lower() == OPAQUE_ORIGIN:
        return False
    candidate = normalize(origin)
    return any(candidate == normalize(item) for item in allowed)
