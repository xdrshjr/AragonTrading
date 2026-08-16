"""HTTP 侧信任边界（trust-boundary-and-signal-integrity D1 / G10）。

被守的东西很具体：`/api/trading/*` 的 8 条路由用**库内明文密钥**向 OKX 下真实委托，而在本轮之前
中间件栈里只有 `CORSMiddleware` 且 `allow_origins=["*"]`——预检实测返回 `ACAO: *` +
`allow-headers: content-type`，即**任意网页**的跨源 JSON POST 是配置上已经批准的动作。

本文件钉住三件事：

1. 纯函数判据两态收敛（缺失放行 / ``null`` 拒绝 / 白名单放行 / 其余拒绝）；
2. 拒绝发生在**进入路由之前**（不碰 DB、不碰 OKX）；
3. `allowed_origin_list()` 不接受 ``*`` 与 ``null``——一个能被一键关掉的安全开关等于没有开关。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.config import Settings
from app.main import app as real_app
from app.security.origin_middleware import OriginGuardMiddleware
from app.security.origins import DEFAULT_ORIGINS, is_allowed_origin, normalize

EVIL = "https://evil.example.com"
ALLOWED = list(DEFAULT_ORIGINS)


# ============================ 纯函数 ============================
def test_normalize_lowercases_scheme_host_and_drops_trailing_slash():
    assert normalize("HTTP://LocalHost:18761/") == "http://localhost:18761"
    assert normalize("  http://127.0.0.1:18761  ") == "http://127.0.0.1:18761"
    assert normalize("") == ""


def test_normalize_keeps_the_port_because_the_port_is_the_whole_point():
    """18760（后端）与 18761（前端）只差一个端口，归一化丢端口即等于放行错的那一个。"""
    assert normalize("http://127.0.0.1:18760") != normalize("http://127.0.0.1:18761")


@pytest.mark.parametrize("origin,expected", [
    (None, True),                              # 缺失 = 非浏览器（fetch/XHR/WS 握手必带 Origin）
    ("", True),
    ("   ", True),
    ("null", False),                           # 不透明源 = 浏览器 sandboxed iframe
    ("NULL", False),                           # 大小写不敏感，否则一个 `NULL` 就绕过去了
    ("http://localhost:18761", True),
    ("http://127.0.0.1:18761/", True),
    ("http://127.0.0.1:18760", False),         # 端口不同即不同源
    (EVIL, False),
    ("http://localhost.evil.com:18761", False),  # 前缀式包含不算命中
])
def test_is_allowed_origin_two_state_table(origin, expected):
    assert is_allowed_origin(origin, ALLOWED) is expected


def test_opaque_origin_is_rejected_even_though_it_looks_like_a_local_page():
    """`Origin: null` 必须拒绝：三行 HTML 的 sandboxed iframe 就能从任意网页发出它。

    ``<iframe sandbox="allow-scripts" srcdoc="<script>fetch('…/api/trading/close-all',…)</script>">``
    ——放行 `null` 等于给整条边界留一个正门。
    """
    assert is_allowed_origin("null", ALLOWED) is False
    assert is_allowed_origin("null", ALLOWED + ["http://anything"]) is False


# ============================ 中间件 ============================
def _guarded_app(allowed: list[str]) -> tuple[FastAPI, dict]:
    """一个只有 `/probe` 的应用 + 一个命中计数器（用来证明「不进路由」）。"""
    hits = {"count": 0}
    app = FastAPI()

    @app.post("/probe")
    async def probe() -> dict:
        hits["count"] += 1
        return {"ok": True}

    app.add_middleware(OriginGuardMiddleware, allowed=allowed)
    return app, hits


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_cross_origin_post_is_rejected_before_the_route_runs():
    app, hits = _guarded_app(ALLOWED)
    async with _client(app) as client:
        resp = await client.post("/probe", headers={"Origin": EVIL}, json={})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"
    assert hits["count"] == 0        # 这一条才是重点：CORS 只藏响应，本层拦的是**执行**


async def test_opaque_origin_post_is_rejected_before_the_route_runs():
    """[P0-1] 单列：`null` 是 sandboxed iframe 的真实取值，放行它整条边界即作废。"""
    app, hits = _guarded_app(ALLOWED)
    async with _client(app) as client:
        resp = await client.post("/probe", headers={"Origin": "null"}, json={})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"
    assert hits["count"] == 0


async def test_allowed_origin_and_missing_origin_both_reach_the_route():
    app, hits = _guarded_app(ALLOWED)
    async with _client(app) as client:
        assert (await client.post("/probe", headers={"Origin": ALLOWED[0]}, json={})).status_code == 200
        assert (await client.post("/probe", json={})).status_code == 200   # 非浏览器（smoke_e2e 等）
    assert hits["count"] == 2


async def test_non_http_scope_passes_straight_through():
    """lifespan / websocket scope 不归本中间件管（WS 由 `api/ws_guard` 在 accept 前守）。

    在这里拦 non-http 会把 lifespan 一起拦掉，应用直接起不来。
    """
    seen: list[str] = []

    async def downstream(scope, receive, send):
        seen.append(scope["type"])

    middleware = OriginGuardMiddleware(downstream, allowed=[])   # 空白名单 = 最严格
    for scope_type in ("lifespan", "websocket"):
        await middleware({"type": scope_type, "headers": [(b"origin", EVIL.encode())]},
                         None, None)
    assert seen == ["lifespan", "websocket"]


# ============================ 真实应用 ============================
async def test_real_app_rejects_cross_origin_trading_write():
    """`POST /api/trading/close-all` 是「一键平掉全部真实持仓」，它必须先被边界挡住。"""
    async with _client(real_app) as client:
        resp = await client.post("/api/trading/close-all", headers={"Origin": EVIL}, json={})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"


async def test_real_app_rejects_opaque_origin_trading_write():
    async with _client(real_app) as client:
        resp = await client.post("/api/trading/close-all", headers={"Origin": "null"}, json={})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"


async def test_real_app_still_serves_requests_without_an_origin_header():
    """回归保护：`scripts/smoke_e2e.py` 与 `verify-dist.mjs` 的 node http 都不带 Origin。"""
    async with _client(real_app) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200


def test_real_app_cors_no_longer_advertises_a_wildcard():
    cors = [mw for mw in real_app.user_middleware if mw.cls.__name__ == "CORSMiddleware"]
    assert len(cors) == 1
    assert "*" not in cors[0].kwargs["allow_origins"]


def test_origin_guard_is_mounted_outside_cors():
    """ASGI 中间件后进先出：`user_middleware[0]` 是最外层，守卫必须比 CORS 更外。

    反过来装的话请求会先被 CORS 处理，而 CORS 对**非预检**请求一律放行到路由——
    执行照旧发生，只是浏览器读不到响应。那正是本轮要消灭的那种「看起来修了」。
    """
    names = [mw.cls.__name__ for mw in real_app.user_middleware]
    assert names.index("OriginGuardMiddleware") < names.index("CORSMiddleware")


# ============================ 配置项 ============================
def test_allowed_origin_list_defaults_to_the_two_local_entrypoints():
    assert Settings(security_allowed_origins="").allowed_origin_list() == list(DEFAULT_ORIGINS)


def test_explicit_value_replaces_the_defaults_instead_of_merging():
    """合并语义会让人「以为收紧了其实没有」，故显式配置即完全替换。"""
    settings = Settings(security_allowed_origins="http://a.test, http://b.test ,")
    assert settings.allowed_origin_list() == ["http://a.test", "http://b.test"]


@pytest.mark.parametrize("raw", ["*", "http://a.test,*", "null", "http://a.test, NULL"])
def test_wildcard_and_null_are_refused_at_startup(raw):
    """一个允许被一键关掉的安全开关等于没有这个开关 —— 构造期就 fail-fast。"""
    with pytest.raises(ValidationError):
        Settings(security_allowed_origins=raw)


@pytest.mark.parametrize("raw", ["*", "null"])
def test_allowed_origin_list_itself_raises_value_error(raw):
    settings = Settings(security_allowed_origins="")
    settings.security_allowed_origins = raw     # 绕过构造期校验，直接考方法本身
    with pytest.raises(ValueError):
        settings.allowed_origin_list()


def test_security_prefix_is_the_contract_with_the_packaged_desktop_backend():
    """字段名必须以 `security_` 开头 ⇒ env 名以 `SECURITY_` 开头。

    `desktop/src/lib/config.js` 的 `backendEnv` 用**前缀白名单**决定哪些 env 进得了打包后端子进程，
    而 prod 的 PyInstaller 后端不读 `.env`、只读进程 env。裸名 `ALLOWED_ORIGINS` 一个前缀都不沾 ⇒
    打包形态下这个开关恒失效，白名单一旦不匹配就是「点什么都 403 且用户无从补救」。
    """
    assert "security_allowed_origins" in Settings.model_fields
