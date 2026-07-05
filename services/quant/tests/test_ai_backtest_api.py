"""AI 回测 REST 单测（ai-auto-trading-backtest §4 / §7.1）：Agent 未配置→400、区间非法→400、
计划轮数越界→400、策略越界→400、start→status→cancel 生命周期，且**双闸无关**（AI_TRADE_ENABLED=false
时回测仍可启动，证回测不校验实盘双闸）。作业协程打桩空转，OKX/LLM 全桩，绝不触网 / 不写真实凭证。
"""
from __future__ import annotations

import asyncio
import time

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.ai_auto.backtest import AiBacktestRunner
from app.ai_auto.backtest_manager import get_ai_backtest_manager
from app.config import Settings, get_settings
from app.db.models import Strategy
from app.db.session import dispose_engine, get_sessionmaker, init_db


async def _idle_run(self, job):
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        job.mark_terminal("cancelled", {"jobId": job.job_id})
        raise


def _load_app():
    from app.main import app
    return app


async def _fresh() -> int:
    await init_db()
    mgr = get_ai_backtest_manager()
    mgr._jobs.clear()
    mgr._reserved = 0
    async with get_sessionmaker()() as s:
        return (await s.execute(select(Strategy.id).order_by(Strategy.id.asc()).limit(1))).scalar_one()


async def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _bt_body(sid: int, **over) -> dict:
    now = int(time.time())
    body = {"instId": "BTC-USDT-SWAP", "bar": "1H", "strategyIds": [sid],
            "startTs": now - 100_000, "endTs": now - 1_000, "decisionEveryBars": 100,
            "contextRefreshRounds": 10}
    body.update(over)
    return body


def _stub_provider(monkeypatch, provider=object()) -> None:
    async def fake_resolve(*_a, **_k):
        return provider

    monkeypatch.setattr("app.ai_auto.backtest_manager.resolve_active_provider", fake_resolve)
    monkeypatch.setattr("app.ai_auto.backtest.make_llm_client_from_config", lambda cfg: object())
    monkeypatch.setattr(AiBacktestRunner, "run", _idle_run)


async def _cleanup(app):
    mgr = get_ai_backtest_manager()
    for job in list(mgr._jobs.values()):
        job.force_cancel()
        await job.join()
    mgr._jobs.clear()
    mgr._reserved = 0
    app.dependency_overrides.clear()
    await dispose_engine()


async def test_start_rejected_when_agent_not_configured(monkeypatch):
    sid = await _fresh()
    app = _load_app()
    _stub_provider(monkeypatch, provider=None)
    try:
        async with await _client(app) as client:
            r = await client.post("/api/ai-trade/backtest/start", json=_bt_body(sid))
            assert r.status_code == 400, r.text
            assert r.json()["error"]["code"] == "AGENT_NOT_CONFIGURED"
    finally:
        await _cleanup(app)


async def test_start_rejected_on_invalid_range(monkeypatch):
    sid = await _fresh()
    app = _load_app()
    _stub_provider(monkeypatch)
    now = int(time.time())
    try:
        async with await _client(app) as client:
            r = await client.post("/api/ai-trade/backtest/start",
                                  json=_bt_body(sid, startTs=now - 1_000, endTs=now - 100_000))
            assert r.status_code == 400, r.text
            assert r.json()["error"]["code"] == "AI_BACKTEST_RANGE_INVALID"
    finally:
        await _cleanup(app)


async def test_start_rejected_on_too_many_rounds(monkeypatch):
    sid = await _fresh()
    app = _load_app()
    _stub_provider(monkeypatch)
    now = int(time.time())
    try:
        async with await _client(app) as client:
            r = await client.post("/api/ai-trade/backtest/start",
                                  json=_bt_body(sid, startTs=now - 30 * 24 * 3600, endTs=now,
                                                decisionEveryBars=1))
            assert r.status_code == 400, r.text
            body = r.json()
            assert body["error"]["code"] == "AI_BACKTEST_TOO_MANY_ROUNDS"
            assert "上限" in body["error"]["message"]        # 含明确指引
    finally:
        await _cleanup(app)


async def test_start_rejected_on_bad_strategy(monkeypatch):
    await _fresh()
    app = _load_app()
    _stub_provider(monkeypatch)
    try:
        async with await _client(app) as client:
            r = await client.post("/api/ai-trade/backtest/start", json=_bt_body(999_999))
            assert r.status_code == 400, r.text
            assert r.json()["error"]["code"] == "AI_TRADE_STRATEGIES_INVALID"
    finally:
        await _cleanup(app)


async def test_lifecycle_start_status_cancel_ignores_ai_gate(monkeypatch):
    """双闸无关：AI_TRADE_ENABLED=false 时回测仍可 start；start→GET status→cancel 生命周期。"""
    sid = await _fresh()
    app = _load_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        ai_trade_enabled=False, live_trading_enabled=False)
    _stub_provider(monkeypatch)
    try:
        async with await _client(app) as client:
            start = await client.post("/api/ai-trade/backtest/start", json=_bt_body(sid))
            assert start.status_code == 200, start.text
            job_id = start.json()["jobId"]
            assert start.json()["plannedRounds"] >= 1

            status = await client.get(f"/api/ai-trade/backtest/{job_id}")
            assert status.status_code == 200, status.text
            assert status.json()["job"]["jobId"] == job_id
            assert status.json()["job"]["state"] == "running"

            cancel = await client.post(f"/api/ai-trade/backtest/{job_id}/cancel")
            assert cancel.status_code == 200, cancel.text

            missing = await client.get("/api/ai-trade/backtest/aibt_missing")
            assert missing.status_code == 404
            assert missing.json()["error"]["code"] == "AI_BACKTEST_JOB_NOT_FOUND"
    finally:
        await _cleanup(app)
