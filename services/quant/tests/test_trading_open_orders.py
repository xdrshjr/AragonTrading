"""未结委托 REST 单测：普通挂单 / 策略委托归一 + 分类 + OCO 展开 + 不守门 + 鉴权失败 502 + instId 透传。

交易客户端全程打桩，不依赖网络。共享临时 DB（conftest 模块级），每用例先清空账户与审计。
**铁律**：一律用占位假凭证，严禁写入真实开发凭证（沿用 test_trading_close 规约）。
"""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.config import Settings, get_settings
from app.db import repositories as repo
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.okx.private_client import OKXPrivateError

# 记录 fake 客户端收到的 instId（C5 透传断言），每用例清空。
_CAPTURED_INST: list[str | None] = []


def _load_app():
    from app.main import app
    return app


async def _fresh() -> None:
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        for row in await repo.list_okx_accounts(session):
            await session.delete(row)
        await session.commit()
    _CAPTURED_INST.clear()


def _override_settings(app, *, live_enabled: bool) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(live_trading_enabled=live_enabled)


async def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _account_body(**over) -> dict:
    body = {"label": "Dev-JR-US", "apiKey": "test-ak-demo-only-c848",
            "secretKey": "test-sk-demo-secret-0001", "passphrase": "test-pp-123456",
            "ipWhitelist": "203.0.113.10", "permissions": "读取/交易"}
    body.update(over)
    return body


# 典型 OKX 原始行：限价平仓（reduceOnly）/ 限价开仓 / 对冲反向平仓 / OCO 随单止盈止损。
_REDUCE_ONLY = {"instId": "BTC-USDT-SWAP", "posSide": "long", "side": "sell", "px": "64000",
                "sz": "3", "state": "live", "reduceOnly": "true", "ordId": "1001", "cTime": "1719500000000"}
_HEDGE_CLOSE = {"instId": "ETH-USDT-SWAP", "posSide": "short", "side": "buy", "px": "3000",
                "sz": "2", "state": "live", "reduceOnly": "false", "ordId": "1002", "cTime": "1719500001000"}
_LIMIT_OPEN = {"instId": "BTC-USDT-SWAP", "posSide": "long", "side": "buy", "px": "60000",
               "sz": "1", "state": "live", "reduceOnly": "false", "ordId": "1003", "cTime": "1719500002000"}
_OCO = {"instId": "BTC-USDT-SWAP", "posSide": "long", "side": "sell", "sz": "5", "state": "live",
        "reduceOnly": "false", "algoId": "987", "tpTriggerPx": "70000", "tpOrdPx": "-1",
        "slTriggerPx": "58000", "slOrdPx": "57900", "cTime": "1719500003000"}


class _FakeTradeClient:
    """OKXTradeClient 桩：注入 orders-pending / orders-algo-pending 三组返回，可整体抛错。"""

    pending: list = [_REDUCE_ONLY, _HEDGE_CLOSE, _LIMIT_OPEN]
    oco: list = [_OCO]
    conditional: list = []
    raise_on_get: OKXPrivateError | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def get_pending_orders(self, inst_id=None):
        _CAPTURED_INST.append(inst_id)
        if self.raise_on_get:
            raise self.raise_on_get
        return list(self.pending)

    async def get_algo_pending_orders(self, ord_type, inst_id=None):
        _CAPTURED_INST.append(inst_id)
        if self.raise_on_get:
            raise self.raise_on_get
        return list(self.oco if ord_type == "oco" else self.conditional)

    async def aclose(self):
        pass


def _patch_trading(monkeypatch, **attrs) -> None:
    cls = type("_Fake", (_FakeTradeClient,), attrs)
    monkeypatch.setattr("app.okx.trading.OKXTradeClient", cls)


async def _seed_account(client) -> int:
    created = await client.post("/api/accounts", json=_account_body())
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def test_pending_orders_classified_into_kinds(monkeypatch):
    """reduceOnly / 对冲反向 → limit_close；普通买开 → limit_open（R4 分类）。"""
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=True)
    _patch_trading(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.get("/api/trading/open-orders")
            assert r.status_code == 200, r.text
            by_ord = {o["ordId"]: o for o in r.json()["orders"] if o["ordId"]}
            assert by_ord["1001"]["kind"] == "limit_close" and by_ord["1001"]["reduceOnly"] is True
            assert by_ord["1002"]["kind"] == "limit_close"   # 对冲 short+buy
            assert by_ord["1003"]["kind"] == "limit_open"    # 普通 long+buy
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_oco_expands_into_tp_and_sl(monkeypatch):
    """一条 OCO（tp+sl）展开为两条；tpOrdPx="-1" → px=None；slOrdPx 数值保留；共享 algoId。"""
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=True)
    _patch_trading(monkeypatch, pending=[], oco=[_OCO], conditional=[])
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.get("/api/trading/open-orders")
            assert r.status_code == 200, r.text
            by_kind = {o["kind"]: o for o in r.json()["orders"]}
            assert set(by_kind) == {"tp", "sl"}
            assert by_kind["tp"]["triggerPx"] == 70000.0 and by_kind["tp"]["px"] is None
            assert by_kind["sl"]["triggerPx"] == 58000.0 and by_kind["sl"]["px"] == 57900.0
            assert by_kind["tp"]["algoId"] == "987" and by_kind["sl"]["algoId"] == "987"
            assert by_kind["tp"]["createdTs"] == 1719500003   # 毫秒 → 秒
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_open_orders_not_gated_by_live_trading_disabled(monkeypatch):
    """读类接口不受 LIVE_TRADING_ENABLED 影响（开关关闭仍 200）。"""
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=False)
    _patch_trading(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.get("/api/trading/open-orders")
            assert r.status_code == 200, r.text
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_open_orders_okx_error_maps_to_502_and_redacts(monkeypatch):
    """OKX 抛错 → 502，error.code 经 _map_trade_code 归一，message 不含密钥。"""
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=True)
    _patch_trading(monkeypatch,
                   raise_on_get=OKXPrivateError("50111", "bad key test-sk-demo-secret-0001"))
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.get("/api/trading/open-orders")
            assert r.status_code == 502, r.text
            body = r.json()
            assert body["error"]["code"] == "OKX_AUTH_KEY"
            assert "test-sk-demo-secret-0001" not in body["error"]["message"]
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_pending_order_exposes_attached_tp_sl(monkeypatch):
    """F-4：挂单回带 attachAlgoOrds（随单 OCO）时透出 tp/sl 触发价；"-1"/缺失 → None（_algo_px）。"""
    with_attach = dict(_LIMIT_OPEN, ordId="1004", cTime="1719500004000",
                       attachAlgoOrds=[{"tpTriggerPx": "70000", "slTriggerPx": "58000"}])
    tp_only_market = dict(_LIMIT_OPEN, ordId="1005", cTime="1719500005000",
                          attachAlgoOrds=[{"tpTriggerPx": "70000", "slTriggerPx": "-1"}])
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=True)
    _patch_trading(monkeypatch, pending=[with_attach, tp_only_market], oco=[], conditional=[])
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.get("/api/trading/open-orders")
            assert r.status_code == 200, r.text
            by_ord = {o["ordId"]: o for o in r.json()["orders"]}
            assert by_ord["1004"]["attachTpTriggerPx"] == 70000.0
            assert by_ord["1004"]["attachSlTriggerPx"] == 58000.0
            assert by_ord["1005"]["attachTpTriggerPx"] == 70000.0
            assert by_ord["1005"]["attachSlTriggerPx"] is None    # "-1" 触发后市价 → None
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_pending_order_without_attach_yields_none_fields(monkeypatch):
    """F-4：无 attachAlgoOrds 的挂单两字段恒 None；策略委托行（tp/sl）同样 None（Pydantic 兜底）。"""
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=True)
    _patch_trading(monkeypatch)          # 默认三行 pending（均无 attach）+ 一条 OCO
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.get("/api/trading/open-orders")
            assert r.status_code == 200, r.text
            for order in r.json()["orders"]:
                assert order["attachTpTriggerPx"] is None
                assert order["attachSlTriggerPx"] is None
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_pending_order_malformed_attach_never_breaks_endpoint(monkeypatch):
    """F-4 畸形态（评审 R-2）：attach 非 list / 空 list / **list 元素非 dict** / 价格畸形。

    `_normalize_pending_order` 跑在列表推导里——任何一行抛异常都会炸掉**整个**端点（持仓子行/
    图表虚线/新页签三处同灭），故畸形一律静默落 None、端点必须仍 200。
    """
    rows = [
        dict(_LIMIT_OPEN, ordId="2001", attachAlgoOrds="oops"),          # 非 list
        dict(_LIMIT_OPEN, ordId="2002", attachAlgoOrds=[]),              # 空 list
        dict(_LIMIT_OPEN, ordId="2003", attachAlgoOrds=["not-a-dict"]),  # 元素非 dict（R-2 核心用例）
        dict(_LIMIT_OPEN, ordId="2004",
             attachAlgoOrds=[{"tpTriggerPx": "abc", "slTriggerPx": ""}]),  # 价格不可解析/空
    ]
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=True)
    _patch_trading(monkeypatch, pending=rows, oco=[], conditional=[])
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.get("/api/trading/open-orders")
            assert r.status_code == 200, r.text
            orders = r.json()["orders"]
            assert len(orders) == 4                       # 无一行被畸形 attach 炸掉
            for order in orders:
                assert order["attachTpTriggerPx"] is None
                assert order["attachSlTriggerPx"] is None
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_open_orders_passes_inst_id_through_to_client(monkeypatch):
    """C5：传 instId 时服务端透传给普通挂单 + 两类策略委托查询（避免未测死入参）。"""
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=True)
    _patch_trading(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.get("/api/trading/open-orders", params={"instId": "BTC-USDT-SWAP"})
            assert r.status_code == 200, r.text
            # 三次查询（pending + oco + conditional）都应收到该 instId。
            assert _CAPTURED_INST == ["BTC-USDT-SWAP", "BTC-USDT-SWAP", "BTC-USDT-SWAP"]
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()
