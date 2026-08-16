"""WebSocket 侧信任边界（trust-boundary-and-signal-integrity D1 / G10）。

**WS 完全不过 CORS**：`starlette/middleware/cors.py:76` 对非 http scope 直接放行。所以就算把
`allow_origins` 收紧到白名单，9 条 `@router.websocket` 路由仍然对任意 `Origin` 敞开——而 WS 的响应
是**可读**的，任意网页里一个 sandboxed iframe（`Origin: null`）就能把用户的实盘会话、成交、持仓
订阅走。HTTP 那侧至少还有「浏览器读不到响应」兜着，这一侧连那层都没有。

**手法**：本仓 WS 用例一律 `_FakeWS` + 直接 `await` 路由协程（禁用 `TestClient`：在已运行的事件
循环里它会与 anyio portal 死锁），模板见 `tests/test_ws_idle_heartbeat.py:24`。因为守卫排在
`accept()` **之前**，被拒的路径根本走不到 manager，所以这里一个 monkeypatch 都不需要。
"""
from __future__ import annotations

import pytest

from app.api import agent as agent_api
from app.api import ai_auto as ai_auto_api
from app.api import discovery as discovery_api
from app.api import live_auto as live_auto_api
from app.api import paper as paper_api
from app.api import weight_batches as weight_batches_api
from app.api import weight_models as weight_models_api
from app.api import ws_market as ws_market_api
from app.api.ws_guard import WS_ORIGIN_NOT_ALLOWED_CODE, guard_ws_origin
from app.security.origins import DEFAULT_ORIGINS

EVIL = "https://evil.example.com"


class _FakeWS:
    """最小 WS 替身：记下发出去的帧、是否 accept、以何 code 关闭。"""

    def __init__(self, origin: str | None = None, path: str = "/api/ws/probe"):
        self.sent: list = []
        self.accepted = False
        self.closed_code: int | None = None
        self.headers: dict = {} if origin is None else {"origin": origin}
        self.scope: dict = {"path": path}
        self.query_params: dict = {}

    async def accept(self):
        self.accepted = True

    async def close(self, code: int = 1000):
        self.closed_code = code

    async def send_text(self, text):
        self.sent.append(text)


# (标签, 模块, 路由协程名, 除 websocket 外的位置参数)
WS_ROUTES = [
    ("agent", agent_api, "ws_agent", ("conv-1",)),
    ("ai-trade", ai_auto_api, "ws_ai_trade", ("sid-1",)),
    ("ai-backtest", ai_auto_api, "ws_ai_backtest", ("job-1",)),
    ("discovery", discovery_api, "ws_discovery", (1,)),
    ("live-auto", live_auto_api, "ws_live_auto", ("sid-1",)),
    ("paper", paper_api, "ws_paper", ("sid-1",)),
    ("weight-batch", weight_batches_api, "ws_weight_batch", (1,)),
    ("weight-train", weight_models_api, "ws_weight_train", ("job-1",)),
    ("market", ws_market_api, "ws_market", ()),
]


def test_every_websocket_route_in_the_repo_is_covered():
    """路由总数是 9 —— 漏守一条就等于整条边界只锁了 8/9 扇门。

    这条断言存在的理由：将来新增一条 `@router.websocket` 而忘了加守卫时，
    `tsc`/`ruff` 都不会红，只有这里会。
    """
    assert len(WS_ROUTES) == 9
    assert len({(module.__name__, name) for _, module, name, _ in WS_ROUTES}) == 9


@pytest.mark.parametrize("label,module,route_name,args", WS_ROUTES,
                         ids=[row[0] for row in WS_ROUTES])
async def test_route_rejects_cross_origin_handshake(label, module, route_name, args):
    ws = _FakeWS(origin=EVIL, path=f"/api/ws/{label}")
    await getattr(module, route_name)(ws, *args)

    assert ws.closed_code == WS_ORIGIN_NOT_ALLOWED_CODE
    assert ws.accepted is False       # accept 之前就关：连一次快照都不许泄漏
    assert ws.sent == []


@pytest.mark.parametrize("label,module,route_name,args", WS_ROUTES,
                         ids=[row[0] for row in WS_ROUTES])
async def test_route_rejects_opaque_origin_handshake(label, module, route_name, args):
    """[P0-1] `Origin: null` 单列 9 条。

    这一组比 HTTP 那一组更要紧：HTTP 上浏览器至少读不到响应，而 WS 没有 CORS，
    帧是**可读**的——放行 `null` 就是把实盘会话直接交给任意网页里的一个 sandboxed iframe。
    """
    ws = _FakeWS(origin="null", path=f"/api/ws/{label}")
    await getattr(module, route_name)(ws, *args)

    assert ws.closed_code == WS_ORIGIN_NOT_ALLOWED_CODE
    assert ws.accepted is False
    assert ws.sent == []


async def test_guard_lets_through_missing_and_whitelisted_origins():
    """缺失 = 非浏览器客户端（`scripts/smoke_e2e.py`、`verify-dist.mjs` 的 node http）。"""
    for origin in (None, DEFAULT_ORIGINS[0], DEFAULT_ORIGINS[1], DEFAULT_ORIGINS[1] + "/"):
        ws = _FakeWS(origin=origin)
        assert await guard_ws_origin(ws) is True
        assert ws.closed_code is None


async def test_allowed_handshake_still_reaches_the_route_body():
    """放行路径必须与本轮之前逐字相同：未知会话仍然是 `accept` → `NOT_FOUND` → 关闭。"""
    ws = _FakeWS(origin=DEFAULT_ORIGINS[0], path="/api/ws/paper")
    await paper_api.ws_paper(ws, "no-such-session")

    assert ws.accepted is True
    assert len(ws.sent) == 1
    assert '"NOT_FOUND"' in ws.sent[0]


async def test_rejection_is_logged_with_the_origin_and_the_path(caplog):
    """403/1008 必须留下定位线索，否则「点什么都连不上」就是一个无法排障的黑箱。"""
    ws = _FakeWS(origin=EVIL, path="/api/ws/ai-trade/abc")
    with caplog.at_level("WARNING", logger="aragon.security"):
        assert await guard_ws_origin(ws) is False

    assert any(EVIL in record.getMessage() and "/api/ws/ai-trade/abc" in record.getMessage()
               for record in caplog.records)
