"""实盘平仓 REST 单测：开关守门 / 限价 + 市价平仓落审计 / 无持仓 + 超额本地拒 / 一键全平逐仓汇总。

交易客户端与公共 rest 全程打桩，不依赖网络。共享临时 DB（conftest 模块级），每用例先清空账户与审计。
**铁律**：一律用占位假凭证，严禁写入真实开发凭证（沿用 test_trading_api 规约）。
"""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.config import Settings, get_settings
from app.db import repositories as repo
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.okx.private_client import OKXPrivateError
from app.okx.trading import (
    _CLOSE_LOCAL_REJECT_CODES,
    _map_trade_code,
)

_SUPPORTED_META = {"ctVal": 0.01, "ctValCcy": "BTC", "lotSz": 1.0, "minSz": 1.0, "tickSz": 0.1,
                   "maxLever": 125.0, "ctType": "linear", "settleCcy": "USDT"}
_DEFAULT_POSITIONS = [{"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": "5", "mgnMode": "cross"}]


def _load_app():
    from app.main import app
    return app


async def _fresh() -> None:
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        for row in await repo.list_okx_accounts(session):
            await session.delete(row)
        for row in await repo.list_live_order_audit(session, limit=1000):
            await session.delete(row)
        await session.commit()


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


def _close_body(**over) -> dict:
    body = {"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": 5, "mgnMode": "cross",
            "ordType": "limit", "px": 64000.0, "sz": 3}
    body.update(over)
    return body


class _FakeTradeClient:
    """OKXTradeClient 桩：按用例注入 posMode / positions / place_order / close_position 行为。"""

    pos_mode: str = "long_short_mode"
    positions: list = _DEFAULT_POSITIONS
    place_result: dict = {"ordId": "987654321", "clOrdId": "aragoncstub"}
    close_result: dict = {"instId": "BTC-USDT-SWAP", "posSide": "long", "clOrdId": "aragoncstub"}
    raise_on_place: OKXPrivateError | None = None
    raise_on_close: OKXPrivateError | None = None
    fail_close_inst: str | None = None   # 一键全平：指定 instId 的 close_position 抛错

    def __init__(self, *args, **kwargs):
        pass

    async def get_account_config(self):
        return {"posMode": self.pos_mode}

    async def get_positions(self):
        return list(self.positions)

    async def place_order(self, payload):
        if self.raise_on_place:
            raise self.raise_on_place
        return self.place_result

    async def close_position(self, inst_id, pos_side, mgn_mode, *, auto_cxl=True, cl_ord_id=None):
        if self.raise_on_close:
            raise self.raise_on_close
        if self.fail_close_inst and inst_id == self.fail_close_inst:
            raise OKXPrivateError("51008", "insufficient")
        return {**self.close_result, "instId": inst_id, "posSide": pos_side, "clOrdId": cl_ord_id}

    async def aclose(self):
        pass


class _FakeRest:
    async def get_instrument_meta(self, inst_type, inst_id):
        return dict(_SUPPORTED_META)

    async def get_ticker(self, inst_id):
        return 64000.0


def _patch_trading(monkeypatch, **attrs) -> None:
    cls = type("_Fake", (_FakeTradeClient,), attrs)
    monkeypatch.setattr("app.okx.trading.OKXTradeClient", cls)
    monkeypatch.setattr("app.okx.trading.get_rest_client", lambda: _FakeRest())


async def _seed_account(client) -> int:
    created = await client.post("/api/accounts", json=_account_body())
    assert created.status_code == 201, created.text
    return created.json()["id"]


# ---------------- _map_trade_code（P1-1 单元） ----------------

def test_map_trade_code_passes_through_close_local_reject_codes():
    """平仓本地拒三码原样透传，不被拍平为 OKX_ERROR（P1-1，§6 测点 3）。"""
    for code in _CLOSE_LOCAL_REJECT_CODES:
        assert _map_trade_code(code) == code


# ---------------- 守门 ----------------

async def test_close_rejected_when_live_trading_disabled():
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=False)
    try:
        async with await _client(app) as client:
            r = await client.post("/api/trading/close", json=_close_body())
            assert r.status_code == 403, r.text
            assert r.json()["error"]["code"] == "LIVE_TRADING_DISABLED"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_close_all_rejected_when_live_trading_disabled():
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=False)
    try:
        async with await _client(app) as client:
            r = await client.post("/api/trading/close-all", json={})
            assert r.status_code == 403, r.text
            assert r.json()["error"]["code"] == "LIVE_TRADING_DISABLED"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


# ---------------- 单仓平仓 ----------------

async def test_limit_close_calls_place_order_and_audits_accepted(monkeypatch):
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=True)
    _patch_trading(monkeypatch)
    try:
        async with await _client(app) as client:
            aid = await _seed_account(client)
            r = await client.post("/api/trading/close", json=_close_body(ordType="limit", sz=3))
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True and body["mode"] == "limit"
            assert body["ordId"] == "987654321" and body["closedSz"] == 3
        sm = get_sessionmaker()
        async with sm() as session:
            rows = await repo.list_live_order_audit(session, account_id=aid)
            assert len(rows) == 1 and rows[0].status == "accepted"
            assert rows[0].ord_type == "limit" and rows[0].direction == "long"
            assert rows[0].cl_ord_id.startswith("aragonc")
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_market_close_calls_close_position_and_backfills_closed_sz(monkeypatch):
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=True)
    _patch_trading(monkeypatch)
    try:
        async with await _client(app) as client:
            aid = await _seed_account(client)
            r = await client.post("/api/trading/close", json=_close_body(ordType="market", px=None, sz=None))
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["mode"] == "market" and body["ordId"] is None
            assert body["closedSz"] == 5          # 回填本地 |pos|
        sm = get_sessionmaker()
        async with sm() as session:
            rows = await repo.list_live_order_audit(session, account_id=aid)
            assert len(rows) == 1 and rows[0].status == "accepted"
            assert rows[0].ord_type == "market" and rows[0].px is None and rows[0].sz == 5
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_close_no_position_returns_502_and_audits_rejected(monkeypatch):
    """该方向无持仓 → LIVE_NO_POSITION，经 502，审计判 rejected（本地拒，P1-1）。"""
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=True)
    _patch_trading(monkeypatch, positions=[])
    try:
        async with await _client(app) as client:
            aid = await _seed_account(client)
            r = await client.post("/api/trading/close", json=_close_body(ordType="market", px=None, sz=None))
            assert r.status_code == 502, r.text
            assert r.json()["error"]["code"] == "LIVE_NO_POSITION"
        sm = get_sessionmaker()
        async with sm() as session:
            rows = await repo.list_live_order_audit(session, account_id=aid)
            assert len(rows) == 1 and rows[0].status == "rejected"
            assert rows[0].error_code == "LIVE_NO_POSITION"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_close_size_exceeding_position_rejected_locally(monkeypatch):
    """sz>|pos| → 本地拒 LIVE_CLOSE_SIZE_EXCEEDS，绝不发往 OKX，审计 rejected。"""
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=True)
    _patch_trading(monkeypatch)
    try:
        async with await _client(app) as client:
            aid = await _seed_account(client)
            r = await client.post("/api/trading/close", json=_close_body(sz=8))  # |pos|=5
            assert r.status_code == 502, r.text
            assert r.json()["error"]["code"] == "LIVE_CLOSE_SIZE_EXCEEDS"
        sm = get_sessionmaker()
        async with sm() as session:
            rows = await repo.list_live_order_audit(session, account_id=aid)
            assert rows[0].status == "rejected" and rows[0].error_code == "LIVE_CLOSE_SIZE_EXCEEDS"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_close_okx_error_maps_and_audits_error(monkeypatch):
    """OKX 业务失败（非本地拒）→ 归一码 + 审计 error（非 rejected）。"""
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=True)
    _patch_trading(monkeypatch, raise_on_place=OKXPrivateError("51008", "insufficient"))
    try:
        async with await _client(app) as client:
            aid = await _seed_account(client)
            r = await client.post("/api/trading/close", json=_close_body(ordType="limit", sz=3))
            assert r.status_code == 502, r.text
            assert r.json()["error"]["code"] == "INSUFFICIENT_BALANCE"
        sm = get_sessionmaker()
        async with sm() as session:
            rows = await repo.list_live_order_audit(session, account_id=aid)
            assert rows[0].status == "error" and rows[0].error_code == "INSUFFICIENT_BALANCE"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


# ---------------- 一键市价全平 ----------------

async def test_close_all_collects_per_position_results_without_aborting(monkeypatch):
    """多仓逐仓平、单仓失败并入 results 且不中断、ok=failedCount==0（R10）。"""
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=True)
    positions = [
        {"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": "5", "mgnMode": "cross"},
        {"instId": "ETH-USDT-SWAP", "posSide": "short", "pos": "-3", "mgnMode": "isolated"},
    ]
    _patch_trading(monkeypatch, positions=positions, fail_close_inst="ETH-USDT-SWAP")
    try:
        async with await _client(app) as client:
            aid = await _seed_account(client)
            r = await client.post("/api/trading/close-all", json={})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is False
            assert body["closedCount"] == 1 and body["failedCount"] == 1
            by_inst = {row["instId"]: row for row in body["results"]}
            assert by_inst["BTC-USDT-SWAP"]["ok"] is True
            assert by_inst["ETH-USDT-SWAP"]["ok"] is False
            assert by_inst["ETH-USDT-SWAP"]["code"] == "INSUFFICIENT_BALANCE"
        sm = get_sessionmaker()
        async with sm() as session:
            rows = await repo.list_live_order_audit(session, account_id=aid)
            statuses = sorted(row.status for row in rows)
            assert statuses == ["accepted", "error"]
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_close_all_empty_positions_returns_zero(monkeypatch):
    await _fresh()
    app = _load_app()
    _override_settings(app, live_enabled=True)
    _patch_trading(monkeypatch, positions=[])
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/trading/close-all", json={})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True and body["closedCount"] == 0 and body["failedCount"] == 0
            assert body["results"] == []
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()
