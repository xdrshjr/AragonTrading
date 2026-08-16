"""实盘交易 REST 单测：开关守门 / 下单成功落审计 / 错误归一 / net_mode 反向拦截 / 默认账户回落。

交易客户端与公共 rest 全程打桩，不依赖网络。共享临时 DB（conftest 模块级），每用例先清空账户与审计。
**铁律**：一律用占位假凭证，严禁写入任务下发的真实开发凭证（沿用 test_accounts_api 规约）。
"""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.config import Settings, get_settings
from app.db import repositories as repo
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.okx.private_client import OKXPrivateError

_SUPPORTED_META = {"ctVal": 0.01, "ctValCcy": "BTC", "lotSz": 1.0, "minSz": 1.0, "tickSz": 0.1,
                   "maxLever": 125.0, "ctType": "linear", "settleCcy": "USDT"}


def _load_app():
    from app.main import app
    return app


async def _fresh() -> None:
    """建表并清空 okx_accounts + live_order_audit（确定空表起步）。"""
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        for row in await repo.list_okx_accounts(session):
            await session.delete(row)
        for row in await repo.list_live_order_audit(session, limit=1000):
            await session.delete(row)
        await session.commit()


def _override_settings(app, *, live_enabled: bool, max_notional: float = 200000.0) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        live_trading_enabled=live_enabled, live_max_order_notional_usd=max_notional)


async def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _account_body(**over) -> dict:
    body = {"label": "Dev-JR-US", "apiKey": "test-ak-demo-only-c848",
            "secretKey": "test-sk-demo-secret-0001", "passphrase": "test-pp-123456",
            "ipWhitelist": "203.0.113.10", "permissions": "读取/交易"}
    body.update(over)
    return body


def _order_body(**over) -> dict:
    body = {"instId": "BTC-USDT-SWAP", "direction": "long", "ordType": "limit",
            "tdMode": "cross", "px": 30000.0, "sz": 1, "lever": 3}
    body.update(over)
    return body


class _FakeTradeClient:
    """OKXTradeClient 桩：按用例注入 posMode / positions / place_order 返回或抛错。"""

    pos_mode: str = "long_short_mode"
    positions: list = []
    balance: dict = {"details": [{"ccy": "USDT", "availBal": "1000"}]}
    place_result: dict = {"ordId": "987654321", "clOrdId": "aragonstub"}
    raise_on_place: OKXPrivateError | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def get_account_config(self):
        return {"posMode": self.pos_mode}

    async def get_balance(self):
        return self.balance

    async def get_positions(self):
        return self.positions

    async def set_leverage(self, *args, **kwargs):
        return {}

    async def place_order(self, payload):
        if self.raise_on_place:
            raise self.raise_on_place
        return self.place_result

    async def aclose(self):
        pass


class _FakeRest:
    async def get_instrument_meta(self, inst_type, inst_id):
        return dict(_SUPPORTED_META)

    async def get_ticker(self, inst_id):
        return 30000.0


def _patch_trading(monkeypatch, **attrs) -> None:
    cls = type("_Fake", (_FakeTradeClient,), attrs)
    monkeypatch.setattr("app.okx.trading.OKXTradeClient", cls)
    monkeypatch.setattr("app.okx.trading.get_rest_client", lambda: _FakeRest())


async def _seed_account(client) -> int:
    created = await client.post("/api/accounts", json=_account_body())
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def test_order_rejected_when_live_trading_disabled():
    """开关关闭 → POST /trading/order 返回 403（先于一切凭证读取，安全护栏）。"""
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=False)
    try:
        async with await _client(app) as client:
            r = await client.post("/api/trading/order", json=_order_body())
            assert r.status_code == 403, r.text
            assert r.json()["error"]["code"] == "LIVE_TRADING_DISABLED"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_order_success_returns_ordid_and_writes_audit(monkeypatch):
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=True)
    _patch_trading(monkeypatch)
    try:
        async with await _client(app) as client:
            aid = await _seed_account(client)
            r = await client.post("/api/trading/order", json=_order_body())
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True and body["ordId"] == "987654321"
        sm = get_sessionmaker()
        async with sm() as session:
            rows = await repo.list_live_order_audit(session, account_id=aid)
            assert len(rows) == 1
            assert rows[0].status == "accepted" and rows[0].ord_id == "987654321"
            assert rows[0].cl_ord_id.startswith("aragon")
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_order_okx_error_maps_to_502_and_audits_error(monkeypatch):
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=True)
    _patch_trading(monkeypatch, raise_on_place=OKXPrivateError("51008", "insufficient"))
    try:
        async with await _client(app) as client:
            aid = await _seed_account(client)
            r = await client.post("/api/trading/order", json=_order_body())
            assert r.status_code == 502, r.text
            assert r.json()["error"]["code"] == "INSUFFICIENT_BALANCE"
        sm = get_sessionmaker()
        async with sm() as session:
            rows = await repo.list_live_order_audit(session, account_id=aid)
            assert len(rows) == 1 and rows[0].status == "error"
            assert rows[0].error_code == "INSUFFICIENT_BALANCE"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_net_mode_reverse_order_locally_rejected(monkeypatch):
    """net_mode 持多仓时下空单 → 本地拒 LIVE_WOULD_REDUCE_POSITION（守不动现有持仓，C0-1）。"""
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=True)
    _patch_trading(monkeypatch, pos_mode="net_mode",
                   positions=[{"instId": "BTC-USDT-SWAP", "pos": "5"}])
    try:
        async with await _client(app) as client:
            aid = await _seed_account(client)
            r = await client.post("/api/trading/order", json=_order_body(direction="short"))
            assert r.status_code == 502, r.text
            assert r.json()["error"]["code"] == "LIVE_WOULD_REDUCE_POSITION"
        sm = get_sessionmaker()
        async with sm() as session:
            rows = await repo.list_live_order_audit(session, account_id=aid)
            assert rows[0].status == "rejected"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_order_uses_default_account_when_account_id_omitted(monkeypatch):
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=True)
    _patch_trading(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)             # 首账户自动设默认
            r = await client.post("/api/trading/order", json=_order_body())  # 不带 accountId
            assert r.status_code == 200, r.text
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_order_missing_account_returns_404(monkeypatch):
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=True)
    _patch_trading(monkeypatch)
    try:
        async with await _client(app) as client:
            r = await client.post("/api/trading/order", json=_order_body(accountId=999999))
            assert r.status_code == 404, r.text
            assert r.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_order_error_message_redacts_secret(monkeypatch):
    """下单错误 message 不得回显 secret 明文（C7，沿用 _redact 单点收口）。"""
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=True)
    leak = "sign error: test-sk-demo-secret-0001 leaked"
    _patch_trading(monkeypatch, raise_on_place=OKXPrivateError("50113", leak))
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/trading/order", json=_order_body())
            assert "test-sk-demo-secret-0001" not in r.text
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_config_returns_context_with_instrument_support(monkeypatch):
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=True)
    _patch_trading(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.get("/api/trading/config", params={"instId": "BTC-USDT-SWAP"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["posMode"] == "long_short_mode"
            assert body["instrumentSupported"] is True
            assert body["availUsdt"] == 1000.0
            assert body["instMeta"]["ctVal"] == 0.01
            assert body["liveTradingEnabled"] is True
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()
