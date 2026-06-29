"""自选自动交易分组单测（watchlist-auto-trade-group §7.1）：分组 start/stop/member-stop、两段式门禁
全量失败聚合、整组回滚、百分比预算分摊、按比例归一止盈止损、单活跃分组独占、默认预算随成员数缩放。

OKX 调用全程打桩；会话用轻量假实现避免 WS / 网络。铁律：占位假凭证，绝不写真实凭证。
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.db import repositories as repo
from app.db.models import Strategy, StrategyRun
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.live_auto.manager import (
    GroupMemberInput,
    GroupStartRequest,
    LiveAutoAllocationError,
    LiveAutoGroupTooLargeError,
    LiveAutoManager,
    get_live_auto_manager,
)
from app.paper.strategy_factory import build_paper_strategy_spec

_BASE_CONFIG = {"instrumentSupported": True, "availUsdt": 1000.0, "posMode": "long_short_mode",
                "instMeta": {"ctVal": 0.01, "lotSz": 0.01, "minSz": 0.01, "tickSz": 0.1,
                             "maxLever": 125.0}, "lastPx": 30000.0}


class _FakeSession:
    """轻量会话桩：start/stop 不触网；可按 instId 注入启动失败以验证整组回滚。"""

    fail_on_start_inst: str | None = None

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
        self.flatten_failed = False
        self.started = False
        self.stopped = False

    async def start(self):
        if _FakeSession.fail_on_start_inst == self.inst_id:
            raise RuntimeError(f"boom {self.inst_id}")
        self.started = True

    async def stop(self, reason="user_stop"):
        self.state = "stopped"
        self.stop_reason = reason
        self.stopped = True
        sm = get_sessionmaker()
        async with sm() as s:
            await repo.finalize_run(s, self.run_id, status="stopped",
                                    metrics={"totalReturn": 0.0}, end_ts=1_700_000_000)
        return {"ok": True, "closedSz": 1}

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
    await init_db()
    get_live_auto_manager()._group = None
    sm = get_sessionmaker()
    async with sm() as session:
        for row in await repo.list_okx_accounts(session):
            await session.delete(row)
        await session.commit()
        return (await session.execute(
            select(Strategy.id).order_by(Strategy.id.asc()).limit(1))).scalar_one()


def _override(app, *, auto: bool = True, live: bool = True) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        live_trading_enabled=live, live_auto_trading_enabled=auto)


async def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _patch_okx(monkeypatch, *, positions=None, config_for=None) -> None:
    async def fake_config(_creds, _account_id, inst_id, *, settings=None):
        return dict((config_for or {}).get(inst_id, _BASE_CONFIG))

    async def fake_positions(*_a, **_k):
        return list(positions or [])

    monkeypatch.setattr("app.live_auto.manager.fetch_trading_config", fake_config)
    monkeypatch.setattr("app.live_auto.manager.fetch_live_positions", fake_positions)
    monkeypatch.setattr("app.live_auto.manager.LiveAutoSession", _FakeSession)


async def _seed_account(client) -> int:
    body = {"label": "Dev-Fake", "apiKey": "ak-fake-0001",
            "secretKey": "sk-fake-secret-0001", "passphrase": "pp-fake-123456"}
    created = await client.post("/api/accounts", json=body)
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _group_body(strategy_id: int, members, **over) -> dict:
    body = {"strategyId": strategy_id, "bar": "1H",
            "members": [{"instId": i, "allocationPct": p} for i, p in members]}
    body.update(over)
    return body


# ---------------- 纯函数：默认预算缩放（P1-1）与止盈止损归一分摊（P1-2）----------------
def test_resolve_budget_group_scales_with_member_count():
    s = Settings(live_trading_enabled=True, live_auto_trading_enabled=True)
    # 留空 + 3 成员：min(1000×1%×3, 1000×5%) = min(30, 50) = 30
    assert LiveAutoManager._resolve_budget_group(None, 1000.0, 3, s) == 30.0
    # N=1 回退到单标的旧默认 avail×1% = 10
    assert LiveAutoManager._resolve_budget_group(None, 1000.0, 1, s) == 10.0
    # 非空按硬上限夹紧：500 → min(500, 50) = 50
    assert LiveAutoManager._resolve_budget_group(500.0, 1000.0, 3, s) == 50.0


def test_scale_threshold_normalizes_by_sum_pct():
    f = LiveAutoManager._scale_threshold
    # Σ=100：各会话阈值之和恒等于总额
    assert f(100.0, 50.0, 100.0) == 50.0
    # Σ<100（40/30/10，Σ=80）：按 /Σpct 归一 → 50 / 37.5 / 12.5，和仍 = 100
    assert f(100.0, 40.0, 80.0) == 50.0
    assert f(100.0, 30.0, 80.0) == 37.5
    assert f(100.0, 10.0, 80.0) == 12.5
    # 留空总额 → 各会话该阈值为 None
    assert f(None, 50.0, 100.0) is None


def test_validate_members_boundaries():
    mgr = LiveAutoManager()
    s = Settings(live_trading_enabled=True, live_auto_trading_enabled=True)
    with pytest.raises(LiveAutoAllocationError) as empty:
        mgr._validate_members([], s)
    assert empty.value.code == "LIVE_AUTO_GROUP_EMPTY"
    with pytest.raises(LiveAutoAllocationError) as over:
        mgr._validate_members([GroupMemberInput("A", 60), GroupMemberInput("B", 60)], s)
    assert over.value.code == "LIVE_AUTO_ALLOCATION_INVALID"
    with pytest.raises(LiveAutoGroupTooLargeError):
        mgr._validate_members([GroupMemberInput(f"X{i}-USDT-SWAP", 5) for i in range(9)], s)


# ---------------- 集成：分组 start / 预算分摊 / 门禁聚合 / 独占 / 停止 ----------------
async def test_group_start_splits_budget(monkeypatch):
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch_okx(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            members = [("BTC-USDT-SWAP", 50), ("ETH-USDT-SWAP", 30), ("SOL-USDT-SWAP", 20)]
            r = await client.post("/api/live-auto/group/start",
                                  json=_group_body(strategy_id, members, totalCapitalUsd=40))
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["totalBudgetUsd"] == 40.0
            by_inst = {m["instId"]: m for m in body["members"]}
            assert by_inst["BTC-USDT-SWAP"]["budgetUsd"] == 20.0   # 40 × 50%
            assert by_inst["ETH-USDT-SWAP"]["budgetUsd"] == 12.0   # 40 × 30%
            assert by_inst["SOL-USDT-SWAP"]["budgetUsd"] == 8.0    # 40 × 20%
            assert all(m["state"] == "running" for m in body["members"])
            status = await client.get("/api/live-auto/group/status")
            assert status.json()["group"]["groupId"] == body["groupId"]
            assert len(status.json()["group"]["members"]) == 3
    finally:
        get_live_auto_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_group_preflight_aggregates_failures(monkeypatch):
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    # DOGE 已有持仓；PEPE 的 minSz 过大 → 预算不足最小下单量。两者同时失败 → 整组拒绝并聚合明细。
    tiny = {**_BASE_CONFIG, "instMeta": {**_BASE_CONFIG["instMeta"], "minSz": 1e9}}
    _patch_okx(monkeypatch,
               positions=[{"instId": "DOGE-USDT-SWAP", "pos": 3.0}],
               config_for={"PEPE-USDT-SWAP": tiny})
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            members = [("DOGE-USDT-SWAP", 50), ("PEPE-USDT-SWAP", 50)]
            r = await client.post("/api/live-auto/group/start",
                                  json=_group_body(strategy_id, members))
            assert r.status_code == 409, r.text
            err = r.json()["error"]
            assert err["code"] == "LIVE_AUTO_GROUP_PREFLIGHT"
            codes = {m["instId"]: m["code"] for m in err["members"]}
            assert codes["DOGE-USDT-SWAP"] == "LIVE_AUTO_PREEXISTING_POSITION"
            assert codes["PEPE-USDT-SWAP"] == "LIVE_AUTO_MEMBER_BUDGET_TOO_SMALL"
            # 整组失败：无任何活跃分组、无下单
            assert get_live_auto_manager()._group is None
            assert (await client.get("/api/live-auto/group/status")).json()["group"] is None
    finally:
        get_live_auto_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_group_too_large_rejected(monkeypatch):
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch_okx(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            members = [(f"X{i}-USDT-SWAP", 10) for i in range(9)]   # 9 > 默认上限 8
            r = await client.post("/api/live-auto/group/start",
                                  json=_group_body(strategy_id, members))
            assert r.status_code == 400, r.text
            assert r.json()["error"]["code"] == "LIVE_AUTO_GROUP_TOO_LARGE"
    finally:
        get_live_auto_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_second_group_rejected_by_session_limit(monkeypatch):
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch_okx(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            members = [("BTC-USDT-SWAP", 100)]
            first = await client.post("/api/live-auto/group/start",
                                      json=_group_body(strategy_id, members))
            assert first.status_code == 200, first.text
            second = await client.post("/api/live-auto/group/start",
                                       json=_group_body(strategy_id, members))
            assert second.status_code == 409, second.text
            assert second.json()["error"]["code"] == "LIVE_AUTO_SESSION_LIMIT"
    finally:
        get_live_auto_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_group_stop_and_member_stop(monkeypatch):
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch_okx(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            members = [("BTC-USDT-SWAP", 40), ("ETH-USDT-SWAP", 30), ("SOL-USDT-SWAP", 30)]
            start = await client.post("/api/live-auto/group/start",
                                      json=_group_body(strategy_id, members, takeProfitUsd=80))
            group_id = start.json()["groupId"]

            # 单成员停止：其余仍运行，分组保留
            ms = await client.post(f"/api/live-auto/group/{group_id}/member/ETH-USDT-SWAP/stop")
            assert ms.status_code == 200, ms.text
            assert ms.json()["flattened"]["ok"] is True
            after = await client.get("/api/live-auto/group/status")
            states = {m["instId"]: m["state"] for m in after.json()["group"]["members"]}
            assert states["ETH-USDT-SWAP"] == "stopped"
            assert states["BTC-USDT-SWAP"] == "running"

            # 整组停止：全部平仓、分组清空
            gs = await client.post(f"/api/live-auto/group/{group_id}/stop")
            assert gs.status_code == 200, gs.text
            assert all(m["flattened"]["ok"] for m in gs.json()["members"])
            assert (await client.get("/api/live-auto/group/status")).json()["group"] is None
    finally:
        get_live_auto_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_group_take_profit_split(monkeypatch):
    """totalTP=80、三成员 40/30/30（Σ=100）→ 各会话 take_profit_usd = 32/24/24（按 pct/Σpct 归一）。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch_okx(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            members = [("BTC-USDT-SWAP", 40), ("ETH-USDT-SWAP", 30), ("SOL-USDT-SWAP", 30)]
            await client.post("/api/live-auto/group/start",
                              json=_group_body(strategy_id, members, takeProfitUsd=80))
            group = get_live_auto_manager()._group
            tp = {m.inst_id: m.session.take_profit_usd for m in group.members}
            assert tp["BTC-USDT-SWAP"] == 32.0
            assert tp["ETH-USDT-SWAP"] == 24.0
            assert tp["SOL-USDT-SWAP"] == 24.0
    finally:
        get_live_auto_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_group_rollback_on_launch_failure(monkeypatch):
    """第二个成员 session.start() 抛错 → 已起的第一个被 stop 平仓、self._group 清空、原异常透传。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch_okx(monkeypatch)
    _FakeSession.fail_on_start_inst = "ETH-USDT-SWAP"
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            sm = get_sessionmaker()
            async with sm() as s:
                account_row = await repo.get_default_okx_account(s)
                strategy_row = await repo.get_strategy_row(s, strategy_id)
                spec = build_paper_strategy_spec(strategy_row)
                settings = Settings(live_trading_enabled=True, live_auto_trading_enabled=True)
                req = GroupStartRequest(
                    strategy_spec=spec, bar="1H",
                    members_in=[GroupMemberInput("BTC-USDT-SWAP", 50),
                                GroupMemberInput("ETH-USDT-SWAP", 50)],
                    account_row=account_row, settings=settings, total_capital_usd=40)
                # 仅审视**本次**新建的 Run（共享临时库含他测遗留的 running Run，故按 id 圈定本组）。
                max_run_before = (await s.execute(select(func.max(StrategyRun.id)))).scalar() or 0
                with pytest.raises(RuntimeError):
                    await get_live_auto_manager().start_group(req)
            assert get_live_auto_manager()._group is None
            # 回滚须落实**本组**每个成员的 Run 终态：_build_group 已为两成员各建 status="running" 的 Run，
            # 回滚后本次新建的 Run **绝不**允许滞留 "running"（起失败成员及其之后成员曾被漏停成孤儿）。
            async with sm() as s2:
                orphan = (await s2.execute(select(StrategyRun.id).where(
                    StrategyRun.id > max_run_before, StrategyRun.status == "running"))).all()
            assert orphan == [], f"回滚后本组仍有未终结的 Run（孤儿）：{orphan}"
    finally:
        _FakeSession.fail_on_start_inst = None
        get_live_auto_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()
