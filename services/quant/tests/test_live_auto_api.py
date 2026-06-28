"""实时自动交易 REST 单测（live-auto-trading §4）：双闸守门 403、前置门禁（既有持仓/会话独占）、
预算硬上限夹紧、start→status→stop 生命周期 + 停止平仓 + finalize_run。

OKX 调用全程打桩；会话用轻量假实现避免 WS/网络。铁律：占位假凭证，绝不写真实凭证。
"""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import Settings, get_settings
from app.db import repositories as repo
from app.db.models import Strategy
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.live_auto.manager import get_live_auto_manager

_CONFIG = {"instrumentSupported": True, "availUsdt": 1000.0, "posMode": "long_short_mode",
           "instMeta": {"ctVal": 0.01, "lotSz": 0.01, "minSz": 0.01, "tickSz": 0.1,
                        "maxLever": 125.0}, "lastPx": 30000.0}


class _FakeSession:
    """轻量会话桩：start/stop 不触网，记录 flatten 调用。"""

    def __init__(self, session_id, run_id, strategy, inst_id, bar, executor, **kw):
        self.session_id = session_id
        self.run_id = run_id
        self.executor = executor
        self.inst_id = inst_id
        self.bar = bar
        self.state = "running"
        self.stop_reason = None
        self.strategy_id = kw.get("strategy_id")
        self.strategy_name = kw.get("strategy_name") or ""
        self.take_profit_usd = kw.get("take_profit_usd")
        self.stop_loss_usd = kw.get("stop_loss_usd")
        self.flattened = False

    async def start(self):
        return None

    async def stop(self, reason="user_stop"):
        self.state = "stopped"
        self.stop_reason = reason
        self.flattened = True
        sm = get_sessionmaker()
        async with sm() as s:
            await repo.finalize_run(s, self.run_id, status="stopped",
                                    metrics={"totalReturn": 0.0}, end_ts=1_700_000_000)
        return {"ok": True, "closedSz": 1, "clOrdId": "aragoncx"}

    def info(self):
        return {"sessionId": self.session_id, "runId": self.run_id, "state": self.state,
                "strategyId": self.strategy_id, "strategyName": self.strategy_name,
                "instId": self.inst_id, "bar": self.bar,
                "budgetUsd": self.executor.budget_usd, "leverage": self.executor.leverage,
                "takeProfitUsd": self.take_profit_usd, "stopLossUsd": self.stop_loss_usd,
                "positionSide": "flat", "sessionPnlUsd": 0.0, "startedTs": 1_700_000_000}


def _load_app():
    from app.main import app
    return app


async def _fresh() -> int:
    """建表 + 清账户 + 重置会话管理器；返回一个可用策略 id。"""
    await init_db()
    get_live_auto_manager()._session = None
    sm = get_sessionmaker()
    async with sm() as session:
        for row in await repo.list_okx_accounts(session):
            await session.delete(row)
        await session.commit()
        strategy_id = (await session.execute(
            select(Strategy.id).order_by(Strategy.id.asc()).limit(1))).scalar_one()
    return strategy_id


def _override(app, *, auto: bool = True, live: bool = True) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        live_trading_enabled=live, live_auto_trading_enabled=auto)


async def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _account_body(**over) -> dict:
    body = {"label": "Dev-Fake", "apiKey": "ak-fake-0001",
            "secretKey": "sk-fake-secret-0001", "passphrase": "pp-fake-123456"}
    body.update(over)
    return body


def _start_body(strategy_id: int, **over) -> dict:
    body = {"strategyId": strategy_id, "instId": "BTC-USDT-SWAP", "bar": "1H"}
    body.update(over)
    return body


def _patch_okx(monkeypatch, *, positions=None, config=None) -> None:
    async def fake_config(*_a, **_k):
        return dict(config or _CONFIG)

    async def fake_positions(*_a, **_k):
        return list(positions or [])

    monkeypatch.setattr("app.live_auto.manager.fetch_trading_config", fake_config)
    monkeypatch.setattr("app.live_auto.manager.fetch_live_positions", fake_positions)
    monkeypatch.setattr("app.live_auto.manager.LiveAutoSession", _FakeSession)


async def _seed_account(client) -> int:
    created = await client.post("/api/accounts", json=_account_body())
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def test_start_rejected_when_auto_disabled():
    strategy_id = await _fresh()
    app = _load_app()
    _override(app, auto=False)
    try:
        async with await _client(app) as client:
            r = await client.post("/api/live-auto/start", json=_start_body(strategy_id))
            assert r.status_code == 403, r.text
            assert r.json()["error"]["code"] == "LIVE_AUTO_TRADING_DISABLED"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_start_rejected_when_live_trading_disabled():
    strategy_id = await _fresh()
    app = _load_app()
    _override(app, auto=True, live=False)
    try:
        async with await _client(app) as client:
            r = await client.post("/api/live-auto/start", json=_start_body(strategy_id))
            assert r.status_code == 403, r.text
            assert r.json()["error"]["code"] == "LIVE_AUTO_TRADING_DISABLED"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_start_rejected_with_preexisting_position(monkeypatch):
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch_okx(monkeypatch, positions=[{"instId": "BTC-USDT-SWAP", "pos": 3.0}])
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/live-auto/start", json=_start_body(strategy_id))
            assert r.status_code == 409, r.text
            assert r.json()["error"]["code"] == "LIVE_AUTO_PREEXISTING_POSITION"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_second_start_rejected_by_session_limit(monkeypatch):
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch_okx(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            first = await client.post("/api/live-auto/start", json=_start_body(strategy_id))
            assert first.status_code == 200, first.text
            second = await client.post("/api/live-auto/start", json=_start_body(strategy_id))
            assert second.status_code == 409, second.text
            assert second.json()["error"]["code"] == "LIVE_AUTO_SESSION_LIMIT"
    finally:
        get_live_auto_manager()._session = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_budget_clamped_to_hard_cap(monkeypatch):
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch_okx(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            # availUsdt=1000, 硬上限 5% → 50；填 500 应被夹到 50（R4）
            r = await client.post("/api/live-auto/start",
                                  json=_start_body(strategy_id, capitalUsd=500.0))
            assert r.status_code == 200, r.text
            assert r.json()["budgetUsd"] == 50.0
    finally:
        get_live_auto_manager()._session = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_start_status_stop_lifecycle(monkeypatch):
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch_okx(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            start = await client.post("/api/live-auto/start", json=_start_body(strategy_id))
            assert start.status_code == 200, start.text
            session_id = start.json()["sessionId"]

            status = await client.get("/api/live-auto/status")
            assert status.status_code == 200
            assert status.json()["session"]["sessionId"] == session_id
            assert status.json()["liveAutoTradingEnabled"] is True

            stop = await client.post(f"/api/live-auto/{session_id}/stop")
            assert stop.status_code == 200, stop.text
            body = stop.json()
            assert body["flattened"]["ok"] is True
            assert body["run"]["status"] == "stopped"
            # 停止后无活跃会话
            after = await client.get("/api/live-auto/status")
            assert after.json()["session"] is None
    finally:
        get_live_auto_manager()._session = None
        app.dependency_overrides.clear()
        await dispose_engine()
