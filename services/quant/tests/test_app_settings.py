"""设置中心 · 自动交易设置单测（auto-trade-budget-and-allocation-settings §6.1）：
默认 100 / 读写往返 / 越界 422 / 缺记录回默认 / repo 往返 / RN-2 effectiveMaxBudgetPct 经分组天花板夹紧。

铁律：临时 DB（conftest），用例间清 app_settings 行避免串扰（R7）。
"""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.config import Settings, get_settings
from app.db import repositories as repo
from app.db.models import AppSetting
from app.db.session import dispose_engine, get_sessionmaker, init_db


def _load_app():
    from app.main import app
    return app


async def _fresh() -> None:
    """建表 + 清空 app_settings 行（用例间隔离）。"""
    await init_db()
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(delete(AppSetting))
        await s.commit()


def _override(app, *, group_cap_frac: float = 1.0) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        live_auto_group_max_capital_frac=group_cap_frac)


async def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_get_returns_default_100_when_unset():
    await _fresh()
    app = _load_app()
    _override(app)
    try:
        async with await _client(app) as client:
            r = await client.get("/api/settings/auto-trade")
            assert r.status_code == 200, r.text
            assert r.json() == {"maxBudgetPct": 100.0, "effectiveMaxBudgetPct": 100.0}
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_put_then_get_roundtrip():
    await _fresh()
    app = _load_app()
    _override(app)
    try:
        async with await _client(app) as client:
            put = await client.put("/api/settings/auto-trade", json={"maxBudgetPct": 30})
            assert put.status_code == 200, put.text
            assert put.json()["maxBudgetPct"] == 30.0
            got = await client.get("/api/settings/auto-trade")
            assert got.json()["maxBudgetPct"] == 30.0
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_put_out_of_range_rejected():
    await _fresh()
    app = _load_app()
    _override(app)
    try:
        async with await _client(app) as client:
            assert (await client.put(
                "/api/settings/auto-trade", json={"maxBudgetPct": 0})).status_code == 422
            assert (await client.put(
                "/api/settings/auto-trade", json={"maxBudgetPct": 101})).status_code == 422
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_repo_set_get_roundtrip():
    await _fresh()
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            assert (await repo.get_auto_trade_settings(s))["maxBudgetPct"] == 100.0
            await repo.set_auto_trade_settings(s, max_budget_pct=42.5)
        async with sm() as s2:
            assert (await repo.get_auto_trade_settings(s2))["maxBudgetPct"] == 42.5
    finally:
        await dispose_engine()


async def test_effective_pct_clamped_by_group_ceiling():
    # RN-2：分组运营天花板 0.2（20%）低于用户配置 80% → effectiveMaxBudgetPct == 20。
    await _fresh()
    app = _load_app()
    _override(app, group_cap_frac=0.2)
    try:
        async with await _client(app) as client:
            await client.put("/api/settings/auto-trade", json={"maxBudgetPct": 80})
            body = (await client.get("/api/settings/auto-trade")).json()
            assert body["maxBudgetPct"] == 80.0
            assert body["effectiveMaxBudgetPct"] == 20.0
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()
