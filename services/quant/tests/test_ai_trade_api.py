"""AI 自动交易 REST 单测（ai-auto-trading §4）：双闸守门 403、Agent 未配置 400、既有持仓 409、
策略/轮数越界 400、与 live-auto 互斥 409、start→status→stop 生命周期 + 预算夹紧 + 停止平仓。

OKX / LLM / 会话全程打桩；铁律：占位假凭证，绝不写真实凭证。
"""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.ai_auto.manager import get_ai_trade_manager
from app.config import Settings, get_settings
from app.db import repositories as repo
from app.db.models import AppSetting, Strategy
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.live_auto.manager import get_live_auto_manager

_START_TS = 1_700_000_000
_CONFIG = {"instrumentSupported": True, "availUsdt": 1000.0, "posMode": "long_short_mode",
           "instMeta": {"ctVal": 0.01, "lotSz": 0.01, "minSz": 0.01, "tickSz": 0.1,
                        "maxLever": 125.0}, "lastPx": 30000.0}


class _FakeSession:
    """轻量会话桩：start/stop 不触网、不 priming；info/snapshot 供 status/WS。"""

    def __init__(self, session_id, run_id, specs, inst_id, bar, executor, decider, **kw):
        self.session_id = session_id
        self.run_id = run_id
        self.executor = executor
        self.inst_id = inst_id
        self.bar = bar
        self.state = "running"
        self.stop_reason = None
        self.strategy_ids = [s.strategy_id for s in specs]
        self.strategy_names = [s.strategy_name for s in specs]
        self.context_refresh_rounds = kw.get("context_refresh_rounds", 10)
        self.take_profit_usd = kw.get("take_profit_usd")
        self.stop_loss_usd = kw.get("stop_loss_usd")

    async def start(self):
        return None

    async def stop(self, reason="user_stop"):
        self.state = "stopped"
        self.stop_reason = reason
        sm = get_sessionmaker()
        async with sm() as s:
            await repo.finalize_run(s, self.run_id, status="stopped",
                                    metrics={"totalReturn": 0.0}, end_ts=_START_TS)
        return {"ok": True, "closedSz": 1, "clOrdId": "aragoncx"}

    def info(self):
        return {"sessionId": self.session_id, "runId": self.run_id, "state": self.state,
                "instId": self.inst_id, "bar": self.bar, "strategyIds": self.strategy_ids,
                "strategyNames": self.strategy_names, "budgetUsd": self.executor.budget_usd,
                "leverage": self.executor.leverage, "takeProfitUsd": self.take_profit_usd,
                "stopLossUsd": self.stop_loss_usd,
                "contextRefreshRounds": self.context_refresh_rounds, "roundsCompleted": 0,
                "roundsSinceSummary": 0, "positionSide": "flat", "sessionPnlUsd": 0.0,
                "startedTs": _START_TS, "lastDecision": None, "contextSummary": None,
                "degraded": False}


def _load_app():
    from app.main import app
    return app


async def _fresh() -> int:
    await init_db()
    get_ai_trade_manager()._session = None
    get_live_auto_manager()._group = None
    sm = get_sessionmaker()
    async with sm() as session:
        for row in await repo.list_okx_accounts(session):
            await session.delete(row)
        await session.execute(delete(AppSetting))
        await session.commit()
        strategy_id = (await session.execute(
            select(Strategy.id).order_by(Strategy.id.asc()).limit(1))).scalar_one()
    return strategy_id


def _override(app, *, ai: bool = True, live: bool = True) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        live_trading_enabled=live, ai_trade_enabled=ai)


async def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _account_body(**over) -> dict:
    body = {"label": "Dev-Fake", "apiKey": "ak-fake-0001",
            "secretKey": "sk-fake-secret-0001", "passphrase": "pp-fake-123456"}
    body.update(over)
    return body


def _start_body(strategy_id: int, **over) -> dict:
    body = {"instId": "BTC-USDT-SWAP", "bar": "1H", "strategyIds": [strategy_id],
            "contextRefreshRounds": 10}
    body.update(over)
    return body


def _patch(monkeypatch, *, positions=None, config=None, provider=object()) -> None:
    async def fake_config(*_a, **_k):
        return dict(config or _CONFIG)

    async def fake_positions(*_a, **_k):
        return list(positions or [])

    async def fake_resolve(*_a, **_k):
        return provider

    monkeypatch.setattr("app.ai_auto.manager.fetch_trading_config", fake_config)
    monkeypatch.setattr("app.ai_auto.manager.fetch_live_positions", fake_positions)
    monkeypatch.setattr("app.ai_auto.manager.resolve_active_provider", fake_resolve)
    monkeypatch.setattr("app.ai_auto.manager.make_llm_client_from_config", lambda cfg: object())
    monkeypatch.setattr("app.ai_auto.manager.AiTradeSession", _FakeSession)


async def _seed_account(client) -> int:
    created = await client.post("/api/accounts", json=_account_body())
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def test_start_rejected_when_ai_disabled():
    strategy_id = await _fresh()
    app = _load_app()
    _override(app, ai=False)
    try:
        async with await _client(app) as client:
            r = await client.post("/api/ai-trade/start", json=_start_body(strategy_id))
            assert r.status_code == 403, r.text
            assert r.json()["error"]["code"] == "AI_TRADE_DISABLED"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_start_rejected_when_live_disabled():
    strategy_id = await _fresh()
    app = _load_app()
    _override(app, ai=True, live=False)
    try:
        async with await _client(app) as client:
            r = await client.post("/api/ai-trade/start", json=_start_body(strategy_id))
            assert r.status_code == 403, r.text
            assert r.json()["error"]["code"] == "AI_TRADE_DISABLED"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_start_rejected_when_agent_not_configured(monkeypatch):
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch, provider=None)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_start_body(strategy_id))
            assert r.status_code == 400, r.text
            assert r.json()["error"]["code"] == "AGENT_NOT_CONFIGURED"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_start_rejected_with_preexisting_position(monkeypatch):
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch, positions=[{"instId": "BTC-USDT-SWAP", "pos": 3.0}])
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_start_body(strategy_id))
            assert r.status_code == 409, r.text
            assert r.json()["error"]["code"] == "AI_TRADE_PREEXISTING_POSITION"
    finally:
        get_ai_trade_manager()._session = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_start_rejected_context_rounds_out_of_range(monkeypatch):
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start",
                                  json=_start_body(strategy_id, contextRefreshRounds=1))
            assert r.status_code == 400, r.text
            assert r.json()["error"]["code"] == "AI_TRADE_CONTEXT_ROUNDS_INVALID"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_start_rejected_nonexistent_strategy(monkeypatch):
    await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_start_body(999999))
            assert r.status_code == 400, r.text
            assert r.json()["error"]["code"] == "AI_TRADE_STRATEGIES_INVALID"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_start_rejected_when_live_auto_active(monkeypatch):
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    monkeypatch.setattr(get_live_auto_manager(), "group_status", lambda: {"groupId": "x"})
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_start_body(strategy_id))
            assert r.status_code == 409, r.text
            assert r.json()["error"]["code"] == "AI_TRADE_SESSION_LIMIT"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_lifecycle_start_status_stop_with_budget_clamp(monkeypatch):
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            start = await client.post("/api/ai-trade/start", json=_start_body(strategy_id))
            assert start.status_code == 200, start.text
            body = start.json()
            # 预算夹紧：留空→ avail(1000)×default_frac(0.01)=10，夹到 ≤ avail×min(1.0,0.05)=50 → 10
            assert abs(body["budgetUsd"] - 10.0) < 1e-6
            sid = body["sessionId"]

            status = await client.get("/api/ai-trade/status")
            assert status.json()["session"]["sessionId"] == sid
            assert status.json()["aiTradeEnabled"] is True

            stop = await client.post(f"/api/ai-trade/{sid}/stop")
            assert stop.status_code == 200, stop.text
            assert stop.json()["flattened"]["ok"] is True
            # 停止后 status 归 None
            assert (await client.get("/api/ai-trade/status")).json()["session"] is None
    finally:
        get_ai_trade_manager()._session = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_second_start_rejected_by_session_limit(monkeypatch):
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            first = await client.post("/api/ai-trade/start", json=_start_body(strategy_id))
            assert first.status_code == 200, first.text
            second = await client.post("/api/ai-trade/start", json=_start_body(strategy_id))
            assert second.status_code == 409, second.text
            assert second.json()["error"]["code"] == "AI_TRADE_SESSION_LIMIT"
    finally:
        get_ai_trade_manager()._session = None
        app.dependency_overrides.clear()
        await dispose_engine()
