"""AI 自动交易「分组」单测（ai-trade-multi-symbol-and-timeframe §8.1）：多标的启动 / 预算切分 /
逐成员预检失败整组拒绝 / 敞口天花板锚定 5%（P0-1）/ 留空默认 1%（P1-1）/ budget-too-small（P1-3）/
与 live-auto 互斥 / status()==group_status() / stop_group 清组不留僵尸（P2-4）/ 单成员等价。

多标的范式镜像 live-auto 分组，成员为**零改动的** AiTradeSession（此处以轻量假实现避免 WS / 网络）。
OKX / LLM / 会话全程打桩；铁律：占位假凭证，绝不写真实凭证。
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.ai_auto.manager import (
    AiTradeAllocationError,
    AiTradeGroupMemberInput,
    AiTradeGroupTooLargeError,
    AiTradeManager,
    get_ai_trade_manager,
)
from app.config import Settings, get_settings
from app.db import repositories as repo
from app.db.models import AppSetting, Strategy
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.live_auto.manager import get_live_auto_manager

_CONFIG = {"instrumentSupported": True, "availUsdt": 1000.0, "posMode": "long_short_mode",
           "instMeta": {"ctVal": 0.01, "lotSz": 0.01, "minSz": 0.01, "tickSz": 0.1,
                        "maxLever": 125.0}, "lastPx": 30000.0}
_START_TS = 1_700_000_000


class _FakeSession:
    """轻量会话桩：start/stop 不触网；info() 供 group_status / _member_row。"""

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
        return {"ok": True, "closedSz": 1}

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
    get_ai_trade_manager()._group = None
    get_live_auto_manager()._group = None
    sm = get_sessionmaker()
    async with sm() as session:
        for row in await repo.list_okx_accounts(session):
            await session.delete(row)
        await session.execute(delete(AppSetting))   # 清自动交易设置，避免共享临时库串扰
        await session.commit()
        return (await session.execute(
            select(Strategy.id).order_by(Strategy.id.asc()).limit(1))).scalar_one()


def _override(app, *, ai: bool = True, live: bool = True) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        live_trading_enabled=live, ai_trade_enabled=ai)


async def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _patch(monkeypatch, *, positions=None, config_for=None, provider=object()) -> None:
    async def fake_config(_creds, _account_id, inst_id, *, settings=None):
        return dict((config_for or {}).get(inst_id, _CONFIG))

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
    body = {"label": "Dev-Fake", "apiKey": "ak-fake-0001",
            "secretKey": "sk-fake-secret-0001", "passphrase": "pp-fake-123456"}
    created = await client.post("/api/accounts", json=body)
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _group_body(strategy_id: int, members, **over) -> dict:
    body = {"strategyIds": [strategy_id], "bar": "1H", "contextRefreshRounds": 10,
            "members": [{"instId": i, "allocationPct": p} for i, p in members]}
    body.update(over)
    return body


# ---------------- 纯函数：参数化预算解析（P0-1/P1-1）与止盈止损归一分摊 ----------------
def test_resolve_budget_group_anchors_ai_ceiling():
    """AI 分组分数：cap=min(maxBudgetPct/100, 5%)、default=1%。留空=avail×1%、填值夹 avail×cap。"""
    f = AiTradeManager._resolve_budget_group
    # 默认 maxBudgetPct=100 → cap=0.05：留空 1% / 填 900 夹到 5%（**非** 100%）
    assert f(None, 1000.0, 0.05, 0.01) == 10.0
    assert f(900.0, 1000.0, 0.05, 0.01) == 50.0
    assert f(30.0, 1000.0, 0.05, 0.01) == 30.0
    # 用户把 maxBudgetPct 收紧到 2% → cap=0.02：留空仍 1%、填值夹 2%
    assert f(None, 1000.0, 0.02, 0.01) == 10.0
    assert f(900.0, 1000.0, 0.02, 0.01) == 20.0


def test_scale_threshold_normalizes_by_sum_pct():
    f = AiTradeManager._scale_threshold
    assert f(100.0, 50.0, 100.0) == 50.0
    assert f(100.0, 40.0, 80.0) == 50.0
    assert f(None, 50.0, 100.0) is None


def test_validate_members_boundaries():
    mgr = AiTradeManager()
    s = Settings(live_trading_enabled=True, ai_trade_enabled=True)
    with pytest.raises(AiTradeAllocationError) as empty:
        mgr._validate_members([], s)
    assert empty.value.code == "AI_TRADE_GROUP_EMPTY"
    with pytest.raises(AiTradeAllocationError) as over:
        mgr._validate_members([AiTradeGroupMemberInput("A", 60),
                               AiTradeGroupMemberInput("B", 60)], s)
    assert over.value.code == "AI_TRADE_ALLOCATION_INVALID"
    with pytest.raises(AiTradeGroupTooLargeError):
        mgr._validate_members(
            [AiTradeGroupMemberInput(f"X{i}-USDT-SWAP", 5) for i in range(6)], s)  # 6 > 5
    # max_ratio 放行 Σ>100
    assert len(mgr._validate_members(
        [AiTradeGroupMemberInput("A-USDT-SWAP", 80),
         AiTradeGroupMemberInput("B-USDT-SWAP", 70)], s, "max_ratio")) == 2


# ---------------- 集成：分组 start / 预算切分 / 敞口天花板 / 预检 / 互斥 / 停止 ----------------
async def test_starts_group_over_multiple_symbols(monkeypatch):
    """2 成员启动成功，各得 budget=total×pct/100；status()==group_status() 且含 2 成员行。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            members = [("BTC-USDT-SWAP", 60), ("ETH-USDT-SWAP", 40)]
            r = await client.post("/api/ai-trade/start",
                                  json=_group_body(strategy_id, members, capitalUsd=40))
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["totalBudgetUsd"] == 40.0
            by_inst = {m["instId"]: m for m in body["members"]}
            assert by_inst["BTC-USDT-SWAP"]["budgetUsd"] == 24.0   # 40 × 60%
            assert by_inst["ETH-USDT-SWAP"]["budgetUsd"] == 16.0   # 40 × 40%
            # status() 就是 group_status()（互斥反查零改动前提）
            mgr = get_ai_trade_manager()
            assert mgr.status() == mgr.group_status()
            assert len(mgr.status()["members"]) == 2
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_total_exposure_capped_at_five_percent(monkeypatch):
    """P0-1 敞口门禁：maxBudgetPct=100、填 900 → total 夹到 avail×5%(=50)，**非** 100%(=1000)。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            members = [("BTC-USDT-SWAP", 50), ("ETH-USDT-SWAP", 30), ("SOL-USDT-SWAP", 20)]
            r = await client.post("/api/ai-trade/start",
                                  json=_group_body(strategy_id, members, capitalUsd=900))
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["totalBudgetUsd"] == 50.0   # avail(1000)×5%，绝非 900 或 1000
            assert sum(m["budgetUsd"] for m in body["members"]) == 50.0
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_blank_capital_defaults_to_one_percent(monkeypatch):
    """P1-1：留空 capitalUsd → total = avail×1%(=10)，非打满 5% 上限。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            members = [("BTC-USDT-SWAP", 100)]
            r = await client.post("/api/ai-trade/start", json=_group_body(strategy_id, members))
            assert r.status_code == 200, r.text
            assert r.json()["totalBudgetUsd"] == 10.0
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_rejects_group_when_any_member_has_position(monkeypatch):
    """某成员预存持仓 → 整组拒绝、failures 指名该 instId，self._group 保持 None（无副作用）。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch, positions=[{"instId": "ETH-USDT-SWAP", "pos": 2.0}])
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            members = [("BTC-USDT-SWAP", 50), ("ETH-USDT-SWAP", 50)]
            r = await client.post("/api/ai-trade/start", json=_group_body(strategy_id, members))
            assert r.status_code == 409, r.text
            err = r.json()["error"]
            assert err["code"] == "AI_TRADE_GROUP_PREFLIGHT"
            codes = {m["instId"]: m["code"] for m in err["members"]}
            assert codes["ETH-USDT-SWAP"] == "AI_TRADE_PREEXISTING_POSITION"
            assert get_ai_trade_manager()._group is None
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_member_budget_too_small_rejects_group(monkeypatch):
    """P1-3 新增码：某成员 total×pct 不足最小下单量 → 整组拒绝、AI_TRADE_MEMBER_BUDGET_TOO_SMALL。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    tiny = {**_CONFIG, "instMeta": {**_CONFIG["instMeta"], "minSz": 1e9}}
    _patch(monkeypatch, config_for={"PEPE-USDT-SWAP": tiny})
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            members = [("BTC-USDT-SWAP", 50), ("PEPE-USDT-SWAP", 50)]
            r = await client.post("/api/ai-trade/start", json=_group_body(strategy_id, members))
            assert r.status_code == 409, r.text
            err = r.json()["error"]
            codes = {m["instId"]: m["code"] for m in err["members"]}
            assert codes["PEPE-USDT-SWAP"] == "AI_TRADE_MEMBER_BUDGET_TOO_SMALL"
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_rejects_over_max_symbols(monkeypatch):
    """成员数 > ai_trade_max_symbols(5) → 400 AI_TRADE_GROUP_TOO_LARGE（Σ≤100 使 schema 先放行）。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            members = [(f"S{i}-USDT-SWAP", 10) for i in range(6)]   # 6 > 5，Σ=60 ≤100
            r = await client.post("/api/ai-trade/start", json=_group_body(strategy_id, members))
            assert r.status_code == 400, r.text
            assert r.json()["error"]["code"] == "AI_TRADE_GROUP_TOO_LARGE"
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_mutual_exclusion_with_live_auto(monkeypatch):
    """live-auto 分组活跃时 AI start_group 抛 AI_TRADE_SESSION_LIMIT（互斥经共享准入锁）。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    monkeypatch.setattr(get_live_auto_manager(), "group_status", lambda: {"groupId": "x"})
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            members = [("BTC-USDT-SWAP", 100)]
            r = await client.post("/api/ai-trade/start", json=_group_body(strategy_id, members))
            assert r.status_code == 409, r.text
            assert r.json()["error"]["code"] == "AI_TRADE_SESSION_LIMIT"
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_second_group_rejected_by_session_limit(monkeypatch):
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            members = [("BTC-USDT-SWAP", 100)]
            first = await client.post("/api/ai-trade/start", json=_group_body(strategy_id, members))
            assert first.status_code == 200, first.text
            second = await client.post("/api/ai-trade/start",
                                       json=_group_body(strategy_id, members))
            assert second.status_code == 409, second.text
            assert second.json()["error"]["code"] == "AI_TRADE_SESSION_LIMIT"
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_stop_group_flattens_all_and_clears_for_mutual_exclusion(monkeypatch):
    """P2-4：stop_group 停所有成员、清分组；随后 status()/group_status() 均 None（无僵尸分组阻塞）。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            members = [("BTC-USDT-SWAP", 40), ("ETH-USDT-SWAP", 30), ("SOL-USDT-SWAP", 30)]
            start = await client.post("/api/ai-trade/start",
                                      json=_group_body(strategy_id, members, takeProfitUsd=80))
            group_id = start.json()["groupId"]

            # 单成员停止：其余仍运行，分组保留
            ms = await client.post(f"/api/ai-trade/{group_id}/member/ETH-USDT-SWAP/stop")
            assert ms.status_code == 200, ms.text
            assert ms.json()["flattened"]["ok"] is True
            states = {m["instId"]: m["state"]
                      for m in (await client.get("/api/ai-trade/status")).json()["group"]["members"]}
            assert states["ETH-USDT-SWAP"] == "stopped"
            assert states["BTC-USDT-SWAP"] == "running"

            # 整组停止：全部平仓、分组清空
            gs = await client.post(f"/api/ai-trade/{group_id}/stop")
            assert gs.status_code == 200, gs.text
            assert all(m["flattened"]["ok"] for m in gs.json()["members"])
            mgr = get_ai_trade_manager()
            assert mgr.status() is None and mgr.group_status() is None   # 无僵尸分组
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_group_take_profit_split(monkeypatch):
    """总止盈 80、三成员 40/30/30（Σ=100）→ 各会话 take_profit_usd = 32/24/24（按 pct/Σpct 归一）。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            members = [("BTC-USDT-SWAP", 40), ("ETH-USDT-SWAP", 30), ("SOL-USDT-SWAP", 30)]
            await client.post("/api/ai-trade/start",
                              json=_group_body(strategy_id, members, takeProfitUsd=80))
            group = get_ai_trade_manager()._group
            tp = {m.inst_id: m.session.take_profit_usd for m in group.members}
            assert tp["BTC-USDT-SWAP"] == 32.0
            assert tp["ETH-USDT-SWAP"] == 24.0
            assert tp["SOL-USDT-SWAP"] == 24.0
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_single_member_group_equivalent(monkeypatch):
    """1 成员、100% 分配、显式 capitalUsd 的分组，其成员行与旧单会话 info() 逐键一致（回归保障）。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            members = [("BTC-USDT-SWAP", 100)]
            r = await client.post("/api/ai-trade/start",
                                  json=_group_body(strategy_id, members, capitalUsd=30))
            assert r.status_code == 200, r.text
            body = r.json()
            member = body["members"][0]
            assert member["budgetUsd"] == 30.0          # 显式给定，非默认 1%
            assert member["allocationPct"] == 100
            assert member["instId"] == "BTC-USDT-SWAP"
            assert member["bar"] == "1H"
            assert member["state"] == "running"
            # 成员行是完整会话行（info() 全字段 + allocationPct）
            assert member["strategyIds"] == [strategy_id]
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()
