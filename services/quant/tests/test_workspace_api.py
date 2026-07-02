"""workspace 偏好持久化单测（watchlist-auto-trade-persistence §7）：
默认空 GET / PUT→GET 往返 / members ⊆ watchlist 不变量丢弃 / 越界夹取（非 422）/ 类型错误 422 /
分配模式回落 / 跨会话持久化 / repo 往返。

铁律（P2-3）：临时 DB（conftest），用例间清 ``app_settings`` 行避免共享临时 DB 跨用例串扰（R7）。
"""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

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


async def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_get_returns_present_false_when_unset():
    await _fresh()
    app = _load_app()
    try:
        async with await _client(app) as client:
            r = await client.get("/api/workspace/watchlist")
            assert r.status_code == 200, r.text
            assert r.json() == {
                "present": False,
                "watchlist": [],
                "autoTradeMembers": [],
                "autoTradeAllocationMode": "even",
                "symbolGroups": [],
            }
    finally:
        await dispose_engine()


async def test_put_then_get_roundtrip():
    await _fresh()
    app = _load_app()
    body = {
        "watchlist": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
        "autoTradeMembers": [{"instId": "BTC-USDT-SWAP", "allocationPct": 50}],
        "autoTradeAllocationMode": "even",
    }
    try:
        async with await _client(app) as client:
            put = await client.put("/api/workspace/watchlist", json=body)
            assert put.status_code == 200, put.text
            assert put.json()["present"] is True
            assert put.json()["watchlist"] == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]

            got = await client.get("/api/workspace/watchlist")
            assert got.status_code == 200, got.text
            data = got.json()
            assert data["present"] is True
            assert data["watchlist"] == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
            assert data["autoTradeMembers"] == [{"instId": "BTC-USDT-SWAP", "allocationPct": 50.0}]
            assert data["autoTradeAllocationMode"] == "even"
    finally:
        await dispose_engine()


async def test_put_drops_member_not_in_watchlist():
    # members ⊆ watchlist 不变量：instId 不在自选内的成员被丢弃，其余保留。
    await _fresh()
    app = _load_app()
    body = {
        "watchlist": ["BTC-USDT-SWAP"],
        "autoTradeMembers": [
            {"instId": "BTC-USDT-SWAP", "allocationPct": 40},
            {"instId": "ETH-USDT-SWAP", "allocationPct": 30},  # 不在 watchlist → 丢弃
        ],
        "autoTradeAllocationMode": "even",
    }
    try:
        async with await _client(app) as client:
            put = await client.put("/api/workspace/watchlist", json=body)
            assert put.status_code == 200, put.text
            members = put.json()["autoTradeMembers"]
            assert members == [{"instId": "BTC-USDT-SWAP", "allocationPct": 40.0}]
    finally:
        await dispose_engine()


async def test_put_out_of_range_pct_clamped_not_rejected():
    # P1-A：类型正确但越界的 allocationPct 被夹到 [0,100]（200 接受），绝不 422。
    await _fresh()
    app = _load_app()
    body = {
        "watchlist": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
        "autoTradeMembers": [
            {"instId": "BTC-USDT-SWAP", "allocationPct": 150},
            {"instId": "ETH-USDT-SWAP", "allocationPct": -5},
        ],
        "autoTradeAllocationMode": "even",
    }
    try:
        async with await _client(app) as client:
            put = await client.put("/api/workspace/watchlist", json=body)
            assert put.status_code == 200, put.text
            members = {m["instId"]: m["allocationPct"] for m in put.json()["autoTradeMembers"]}
            assert members["BTC-USDT-SWAP"] == 100.0
            assert members["ETH-USDT-SWAP"] == 0.0
    finally:
        await dispose_engine()


async def test_put_wrong_typed_field_returns_422():
    # P1-A：类型错误（pct 是字符串、watchlist 不是数组）由 Pydantic 拒为 422。
    await _fresh()
    app = _load_app()
    try:
        async with await _client(app) as client:
            bad_pct = {
                "watchlist": ["BTC-USDT-SWAP"],
                "autoTradeMembers": [{"instId": "BTC-USDT-SWAP", "allocationPct": "abc"}],
                "autoTradeAllocationMode": "even",
            }
            assert (await client.put("/api/workspace/watchlist", json=bad_pct)).status_code == 422
            bad_watchlist = {
                "watchlist": 5,
                "autoTradeMembers": [],
                "autoTradeAllocationMode": "even",
            }
            assert (await client.put("/api/workspace/watchlist", json=bad_watchlist)).status_code == 422
    finally:
        await dispose_engine()


async def test_put_bogus_allocation_mode_falls_back_to_even():
    await _fresh()
    app = _load_app()
    body = {"watchlist": [], "autoTradeMembers": [], "autoTradeAllocationMode": "bogus"}
    try:
        async with await _client(app) as client:
            put = await client.put("/api/workspace/watchlist", json=body)
            assert put.status_code == 200, put.text
            assert put.json()["autoTradeAllocationMode"] == "even"
    finally:
        await dispose_engine()


async def test_saved_empty_watchlist_is_present_true():
    # 显式 present 标志：保存为空 ≠ 从未保存（前端据此 respect 空表而非重新 seed 默认）。
    await _fresh()
    app = _load_app()
    body = {"watchlist": [], "autoTradeMembers": [], "autoTradeAllocationMode": "even"}
    try:
        async with await _client(app) as client:
            await client.put("/api/workspace/watchlist", json=body)
            got = (await client.get("/api/workspace/watchlist")).json()
            assert got["present"] is True
            assert got["watchlist"] == []
    finally:
        await dispose_engine()


async def test_persistence_across_fresh_session():
    # 写入后弃用 client（新会话）再读 → 值仍在（DB 同步 commit，崩溃安全的根本目的）。
    await _fresh()
    app = _load_app()
    body = {
        "watchlist": ["BTC-USDT-SWAP"],
        "autoTradeMembers": [{"instId": "BTC-USDT-SWAP", "allocationPct": 25}],
        "autoTradeAllocationMode": "max_ratio",
    }
    try:
        async with await _client(app) as client:
            await client.put("/api/workspace/watchlist", json=body)
        async with await _client(app) as client2:
            got = (await client2.get("/api/workspace/watchlist")).json()
            assert got["watchlist"] == ["BTC-USDT-SWAP"]
            assert got["autoTradeMembers"] == [{"instId": "BTC-USDT-SWAP", "allocationPct": 25.0}]
            assert got["autoTradeAllocationMode"] == "max_ratio"
    finally:
        await dispose_engine()


async def test_repo_set_get_roundtrip_and_sanitize():
    # repo 层往返 + 越界 / 不变量夹取（绕过 HTTP，直接验证 sanitizer 单一执行点）。
    await _fresh()
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            assert await repo.get_workspace_watchlist(s) is None
            doc = await repo.set_workspace_watchlist(
                s,
                watchlist=["BTC-USDT-SWAP", "BTC-USDT-SWAP", "  ", "ETH-USDT-SWAP"],
                members=[
                    {"instId": "BTC-USDT-SWAP", "allocationPct": 150},
                    {"instId": "DOGE-USDT-SWAP", "allocationPct": 10},  # 不在 watchlist → 丢弃
                ],
                allocation_mode="bogus",
            )
            assert doc["watchlist"] == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]  # 去重 + 去空白
            assert doc["autoTradeMembers"] == [{"instId": "BTC-USDT-SWAP", "allocationPct": 100.0}]
            assert doc["autoTradeAllocationMode"] == "even"
        async with sm() as s2:
            got = await repo.get_workspace_watchlist(s2)
            assert got == doc
    finally:
        await dispose_engine()


async def test_symbol_groups_roundtrip_and_sanitize():
    # 标的组 PUT/GET 往返 + sanitizer：id 去重、组名截断、成员去重去空、丢空组、成员**不**按自选裁剪（§3.2 R4）。
    await _fresh()
    app = _load_app()
    body = {
        "watchlist": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
        "autoTradeMembers": [],
        "autoTradeAllocationMode": "even",
        "symbolGroups": [
            {"id": "g1", "name": "主流币",
             "symbols": ["BTC-USDT-SWAP", "BTC-USDT-SWAP", "  ", "DOGE-USDT-SWAP"],  # 去重去空 + 保留非自选成员
             "createdTs": 100, "updatedTs": 200},
            {"id": "g1", "name": "重复 id 丢弃", "symbols": ["ETH-USDT-SWAP"]},          # 同 id → 丢弃
            {"id": "g2", "name": "", "symbols": ["ETH-USDT-SWAP"]},                    # 空名 → 丢弃
            {"id": "g3", "name": "空组", "symbols": []},                                 # 空成员 → 丢弃
        ],
    }
    try:
        async with await _client(app) as client:
            put = await client.put("/api/workspace/watchlist", json=body)
            assert put.status_code == 200, put.text
            groups = put.json()["symbolGroups"]
            assert len(groups) == 1
            assert groups[0]["id"] == "g1"
            assert groups[0]["name"] == "主流币"
            # 成员不因不在自选（DOGE）被裁剪；去重去空后保序。
            assert groups[0]["symbols"] == ["BTC-USDT-SWAP", "DOGE-USDT-SWAP"]
            assert groups[0]["createdTs"] == 100 and groups[0]["updatedTs"] == 200

            got = (await client.get("/api/workspace/watchlist")).json()
            assert got["symbolGroups"] == groups   # GET 回读经同一 sanitizer，稳定
    finally:
        await dispose_engine()
