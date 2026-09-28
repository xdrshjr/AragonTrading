"""REST 契约独立于模型服务，严格中文错误且不回显输入。"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.test_agent_trading_intents import intent_values, store as store


@pytest.mark.asyncio
async def test_router_error_contract_and_cross_conversation(store, monkeypatch):
    from app.api import agent_trading
    from app.db.agent_trade_repositories import AgentTradeRepository

    monkeypatch.setattr(agent_trading, "get_sessionmaker", lambda: store)
    app = FastAPI()
    app.include_router(agent_trading.router, prefix="/api")
    row = await AgentTradeRepository(store).create_intent(intent_values())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.put(
            "/api/agent/conversations/chat/trading-binding",
            json={
                "accountId": "SECRET",
                "accountMode": "live",
                "expectedVersion": 0,
            },
        )
        assert response.status_code == 400
        assert "SECRET" not in response.text
        assert response.json()["error"]["code"] == "INVALID_TRADING_REQUEST"
        response = await client.get(
            f"/api/agent/conversations/other/trade-intents/{row.id}"
        )
        assert response.status_code == 404
        response = await client.get(
            f"/api/agent/conversations/chat/trade-intents/{row.id}"
        )
        assert response.json()["id"] == row.id
        assert "credential" not in response.text
        response = await client.post(
            f"/api/agent/conversations/chat/trade-intents/{row.id}/dismiss",
            json={"version": 1},
        )
        assert response.json()["status"] == "dismissed"


@pytest.mark.asyncio
async def test_card_cannot_execute_when_frozen_binding_is_missing(
    store, monkeypatch
):
    from app.api import agent_trading
    from app.agent_trading import admission
    from app.db.agent_trade_repositories import AgentTradeRepository
    from types import SimpleNamespace

    monkeypatch.setattr(agent_trading, "get_sessionmaker", lambda: store)
    monkeypatch.setattr(admission, "is_ready", lambda: True)
    import app.config

    monkeypatch.setattr(
        app.config,
        "get_settings",
        lambda: SimpleNamespace(
            agent_trading_enabled=True,
            agent_trading_execution_enabled=True,
            live_trading_enabled=True,
        ),
    )
    row = await AgentTradeRepository(store).create_intent(intent_values())
    app = FastAPI()
    app.include_router(agent_trading.router, prefix="/api")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/api/agent/conversations/chat/trade-intents/{row.id}"
        )
    assert response.json()["canExecute"] is False


@pytest.mark.asyncio
async def test_full_app_origin_guard_rejects_execute_before_any_side_effect(
    store, monkeypatch
):
    from sqlalchemy import func, select
    from app.main import app
    from app.api import agent_trading
    from app.agent_trading import execution
    from app.db.agent_trade_models import AgentTradeEvent
    from app.db.agent_trade_repositories import AgentTradeRepository
    from app.security.origin_middleware import OriginGuardMiddleware

    assert any(
        item.cls is OriginGuardMiddleware for item in app.user_middleware
    )
    monkeypatch.setattr(agent_trading, "get_sessionmaker", lambda: store)
    repository = AgentTradeRepository(store)
    row = await repository.create_intent(intent_values())
    dispatched = []

    async def fake_execute(*args):
        dispatched.append(args)
        return row

    monkeypatch.setattr(execution, "execute_intent", fake_execute)
    endpoint = f"/api/agent/conversations/chat/trade-intents/{row.id}/execute"
    body = {"version": row.version, "previewHash": row.preview_hash}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        rejected = await client.post(
            endpoint,
            json=body,
            headers={"Origin": "https://untrusted-agent-trading.invalid"},
        )
        assert rejected.status_code == 403
        assert rejected.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"
        assert dispatched == []
        unchanged = await repository.get_intent(row.id)
        assert (unchanged.status, unchanged.version) == ("prepared", 1)
        async with store() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(AgentTradeEvent)
                )
                == 1
            )
        # 无 Origin 的旧非浏览器客户端仍进入真实路由，但执行端为假服务。
        allowed = await client.post(endpoint, json=body)
        assert allowed.status_code == 202
        assert len(dispatched) == 1
