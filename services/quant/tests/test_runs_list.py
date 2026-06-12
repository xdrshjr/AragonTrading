"""GET /runs 列表单测 (F4)：过滤 + 分页 + id 降序（无网络）。"""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient


def _load_app():
    from app.main import app
    return app


async def test_runs_list_filters_and_pagination():
    from app.db import repositories as repo
    from app.db.session import dispose_engine, get_sessionmaker, init_db
    await init_db()

    sm = get_sessionmaker()
    async with sm() as s:
        strategies = await repo.list_strategies(s)
    sid_a = strategies[0]["id"]
    sid_b = strategies[1]["id"]

    async with sm() as s:
        await repo.create_run(s, strategy_id=sid_a, inst_id="BTC-USDT-SWAP", bar="1H",
                              mode="backtest", config={})
        await repo.create_run(s, strategy_id=sid_a, inst_id="ETH-USDT-SWAP", bar="1H",
                              mode="paper", config={})
        await repo.create_run(s, strategy_id=sid_b, inst_id="BTC-USDT-SWAP", bar="4H",
                              mode="backtest", config={})

    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 全量：id 降序（最新在前）
            r = await client.get("/api/runs")
            assert r.status_code == 200
            runs = r.json()
            assert len(runs) >= 3
            ids = [x["id"] for x in runs]
            assert ids == sorted(ids, reverse=True)

            # mode 过滤
            r = await client.get("/api/runs?mode=paper")
            assert all(x["mode"] == "paper" for x in r.json())

            # strategyId 过滤
            r = await client.get(f"/api/runs?strategyId={sid_b}")
            assert all(x["strategyId"] == sid_b for x in r.json())

            # instId 过滤
            r = await client.get("/api/runs?instId=ETH-USDT-SWAP")
            assert all(x["instId"] == "ETH-USDT-SWAP" for x in r.json())

            # 分页
            r = await client.get("/api/runs?limit=1")
            assert len(r.json()) == 1

            # 越界 limit -> 422
            r = await client.get("/api/runs?limit=9999")
            assert r.status_code == 422
    finally:
        await dispose_engine()
