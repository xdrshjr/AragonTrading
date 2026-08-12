"""AI 自动交易 REST 单测（ai-trade-multi-symbol-and-timeframe §4）：双闸守门 403、Agent 未配置 400、
既有持仓整组拒绝 409（AI_TRADE_GROUP_PREFLIGHT）、策略/轮数越界 400、与 live-auto 互斥 409、
分组 start→status→stop 生命周期 + 预算夹紧 + 停止平仓 + 单仓 OCO pct 夹紧。

start/status/stop 均为「分组」契约（单标的=1 成员 100% 分配分组）。OKX / LLM / 会话全程打桩；
铁律：占位假凭证，绝不写真实凭证。
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
        # discipline-preset R-2：暖机根数会被服务端抬升，桩须留住它才验得到「抬升真的落到了会话」。
        self.priming_bars = kw.get("priming_bars")

    async def start(self):
        return None

    def discipline_wire(self):
        """生效纪律自检的会话桩（discipline-preset 轴 B）：桩不装配任何层 ⇒ 空行 + 无提示。

        `group_status()` 会调它——真实 `AiTradeSession` 新增的**被 manager 读到的**方法，
        测试替身必须同步跟上，否则守的是一个与生产不同构的接口。
        """
        return {"rows": [], "hint": None}

    def acceptance_bundle(self):
        """验收闭环四键的会话桩（horizon-acceptance）：无成交样本 ⇒ 四键恒 None。"""
        return {"acceptance": None, "exits": None, "funding": None,
                "acceptanceInput": None}

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
    get_ai_trade_manager()._group = None
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
    """分组启动体（单标的=1 成员 100% 分配分组）。members 可经 over 覆盖为多标的。"""
    body = {"members": [{"instId": "BTC-USDT-SWAP", "allocationPct": 100}], "bar": "1H",
            "strategyIds": [strategy_id], "contextRefreshRounds": 10}
    body.update(over)
    return body


def _patch(monkeypatch, *, positions=None, config=None, provider=object(),
           pending=None) -> None:
    async def fake_config(*_a, **_k):
        return dict(config or _CONFIG)

    async def fake_positions(*_a, **_k):
        return list(positions or [])

    async def fake_open_orders(*_a, **_k):
        return list(pending or [])

    async def fake_resolve(*_a, **_k):
        return provider

    monkeypatch.setattr("app.ai_auto.manager.fetch_trading_config", fake_config)
    monkeypatch.setattr("app.ai_auto.manager.fetch_live_positions", fake_positions)
    # ai-trade-limit-orders §2.9：启动前置校验新增「已有普通挂单即拒」，须一并桩掉，否则用例出网。
    monkeypatch.setattr("app.ai_auto.manager.fetch_live_open_orders", fake_open_orders)
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


async def test_start_rejects_12h_before_group_side_effects():
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    try:
        async with await _client(app) as client:
            response = await client.post(
                "/api/ai-trade/start",
                json=_start_body(strategy_id, bar="12H"),
            )
            assert response.status_code == 422, response.text
            assert get_ai_trade_manager()._group is None
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
            err = r.json()["error"]
            assert err["code"] == "AI_TRADE_GROUP_PREFLIGHT"
            codes = {m["instId"]: m["code"] for m in err["members"]}
            assert codes["BTC-USDT-SWAP"] == "AI_TRADE_PREEXISTING_POSITION"
            assert get_ai_trade_manager()._group is None
    finally:
        get_ai_trade_manager()._group = None
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
            group_id = body["groupId"]
            member = body["members"][0]
            # 预算夹紧：留空→ avail(1000)×default_frac(0.01)=10，夹到 ≤ avail×min(1.0,0.05)=50 → 10
            assert abs(member["budgetUsd"] - 10.0) < 1e-6
            assert member["allocationPct"] == 100

            status = await client.get("/api/ai-trade/status")
            assert status.json()["group"]["groupId"] == group_id
            assert status.json()["aiTradeEnabled"] is True

            stop = await client.post(f"/api/ai-trade/{group_id}/stop")
            assert stop.status_code == 200, stop.text
            assert stop.json()["members"][0]["flattened"]["ok"] is True
            # 停止后 status 归 None
            assert (await client.get("/api/ai-trade/status")).json()["group"] is None
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_start_clamps_out_of_range_per_position_pct(monkeypatch):
    """E4：越界单仓 pct（0.9）→ executor 生效值夹紧到 ai_trade_max_sl/tp_pct，回执如实告知。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start",
                                  json=_start_body(strategy_id, stopLossPct=0.9, takeProfitPct=0.9))
            assert r.status_code == 200, r.text
            # 回执为分组只读视图（无顶层 pct）；生效 pct 从成员会话 executor.risk 核验（服务端夹紧后）。
            member = get_ai_trade_manager()._group.members[0]
            assert abs(member.session.executor.risk.stop_loss_pct - 0.20) < 1e-9  # ai_trade_max_sl_pct
            assert abs(member.session.executor.risk.take_profit_pct - 0.50) < 1e-9  # ai_trade_max_tp_pct
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_start_pct_falls_back_to_config_default_when_omitted(monkeypatch):
    """E4：留空 stopLossPct/takeProfitPct → executor 回落 config 默认（0.05 / 0.10），回执含默认值。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_start_body(strategy_id))
            assert r.status_code == 200, r.text
            member = get_ai_trade_manager()._group.members[0]
            assert abs(member.session.executor.risk.stop_loss_pct - 0.05) < 1e-9   # default_sl_pct
            assert abs(member.session.executor.risk.take_profit_pct - 0.10) < 1e-9  # default_tp_pct
    finally:
        get_ai_trade_manager()._group = None
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
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


# ---------- 【基础策略】默认开启语义（ai-trade-base-strategy-ma-band §6.1 用例 21~24 / P1-5）----------
async def test_base_rules_default_on_when_field_absent(monkeypatch):
    """请求不带 `baseRules`（含桌面旧包 / 老客户端）⇒ 按 settings 装配为**开启**态，回执如实镜像。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_start_body(strategy_id))
            assert r.status_code == 200, r.text
            assert r.json()["baseRules"] == {"maBandEntry": {
                "enabled": True, "period": 20, "stdMult": 2.0,
                "tolerance": 0.15, "directional": False}}
            rule = get_ai_trade_manager()._group.base_rule
            assert rule is not None and rule.enabled and rule.period == 20
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_explicit_disable_turns_rule_off(monkeypatch):
    """启动弹窗取消勾选 ⇒ `{"maBandEntry":{"enabled":false}}` ⇒ 该会话不装配、回执 baseRules 为 null。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_start_body(
                strategy_id, baseRules={"maBandEntry": {"enabled": False}}))
            assert r.status_code == 200, r.text
            assert r.json()["baseRules"] is None
            assert get_ai_trade_manager()._group.base_rule is None
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_master_switch_off_never_assembles(monkeypatch):
    """运维总闸关 ⇒ 即便请求显式开启也恒不装配（逐字回退到本特性合入前）。"""
    strategy_id = await _fresh()
    app = _load_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        live_trading_enabled=True, ai_trade_enabled=True, ai_trade_base_rules_enabled=False)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_start_body(
                strategy_id, baseRules={"maBandEntry": {"enabled": True, "tolerance": 0.1}}))
            assert r.status_code == 200, r.text
            assert r.json()["baseRules"] is None
            assert get_ai_trade_manager()._group.base_rule is None
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_out_of_range_tolerance_returns_422(monkeypatch):
    """容差越界（>0.25，规则会退化为形同虚设）在入口即 422（与既有 leverage 同惯例）。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_start_body(
                strategy_id, baseRules={"maBandEntry": {"enabled": True, "tolerance": 0.4}}))
            assert r.status_code == 422, r.text
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


# ---------- 利润保护默认开启语义（ai-trade-profit-protection §7.2 / §4.1）----------
async def test_profit_guard_default_on_when_field_absent(monkeypatch):
    """请求不带 `profitGuard`（含桌面旧包 / 老客户端）⇒ 按 settings 装配为**开启**态，回执如实镜像。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_start_body(strategy_id))
            assert r.status_code == 200, r.text
            guard = r.json()["profitGuard"]
            assert guard["enabled"] is True and guard["armReturnPct"] == 0.05
            assert guard["givebackPct"] == 0.40
            # discipline-preset-and-integrity [P1-2] / G-13：默认预设 `balanced` 会把硬止盈从
            # config 默认的 0.0 抬到 0.50。这**不是**回归——它恰恰证明 `_resolve_discipline` 排在
            # `_resolve_profit_guard` **之前**：顺序一旦写反，这里会静默变回 0.0 且不报任何错。
            assert guard["hardTakeProfitReturnPct"] == 0.50
            rule = get_ai_trade_manager()._group.profit_rule
            assert rule is not None and rule.enabled and rule.min_hold_s == 30.0
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_profit_guard_explicit_disable_turns_layer_off(monkeypatch):
    """启动弹窗取消勾选 ⇒ `{"enabled":false}` ⇒ 该会话不装配、回执 profitGuard 为 null。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_start_body(
                strategy_id, profitGuard={"enabled": False}))
            assert r.status_code == 200, r.text
            assert r.json()["profitGuard"] is None
            assert get_ai_trade_manager()._group.profit_rule is None
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_profit_guard_master_switch_off_never_assembles(monkeypatch):
    """运维总闸关 ⇒ 即便请求显式开启也恒不装配（逐字回退到本特性合入前，验收 #10）。"""
    strategy_id = await _fresh()
    app = _load_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        live_trading_enabled=True, ai_trade_enabled=True,
        ai_trade_profit_guard_enabled=False)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_start_body(
                strategy_id, profitGuard={"enabled": True, "armReturnPct": 0.1}))
            assert r.status_code == 200, r.text
            assert r.json()["profitGuard"] is None
            assert get_ai_trade_manager()._group.profit_rule is None
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_profit_guard_custom_values_are_honored(monkeypatch):
    """显式入参逐字生效（只回填缺省字段，不复夹）；留空的 giveback 回落 settings 默认。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_start_body(
                strategy_id, profitGuard={"enabled": True, "armReturnPct": 0.08,
                                          "minHoldSeconds": 0}))
            assert r.status_code == 200, r.text
            guard = r.json()["profitGuard"]
            assert guard["armReturnPct"] == 0.08 and guard["minHoldSeconds"] == 0.0
            assert guard["givebackPct"] == 0.40
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_profit_guard_out_of_range_giveback_returns_422(monkeypatch):
    """回吐比例越界（>1，语义上不成立）在入口即 422（边界在 schema，API 层不复夹）。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_start_body(
                strategy_id, profitGuard={"enabled": True, "givebackPct": 1.5}))
            assert r.status_code == 422, r.text
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_backtest_start_assembles_profit_guard_unless_switch_off(monkeypatch):
    """[adaptive §7.2a I-1] 本轮**有意反转** v1 的 P2-4 决策：回测也接 `profitGuard`。

    第 1 轮把回测列为非目标，是因为当时只想得到「用收盘价近似」这一种做法（低估触发频次 ⇒ 假数据）；
    第 2 轮 F3 改用 bar 内 OHLC 极值 + 保守成交序，故限制解除。本用例同时钉死一键回退：运维总闸
    `ai_trade_profit_guard_backtest_enabled=false` ⇒ 解析结果恒 `None`（回测逐字回到第 1 轮行为）。
    """
    from app.api.ai_auto import _resolve_backtest_profit_guard
    from app.schemas import AiBacktestStartIn, AiTradeProfitGuardIn

    assert "profitGuard" in AiBacktestStartIn.model_fields

    body = AiTradeProfitGuardIn(enabled=True, armReturnPct=0.08)
    rule = _resolve_backtest_profit_guard(body, Settings())
    assert rule is not None and rule.arm_return_pct == 0.08

    off = Settings(ai_trade_profit_guard_backtest_enabled=False)
    assert _resolve_backtest_profit_guard(body, off) is None


async def test_profit_guard_non_monotonic_tiers_return_422(monkeypatch):
    """R-2：阶梯回吐比例非单调不增（赚得越多允许回吐更多）在入口即 422——写反等于把 bug 写进配置。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_start_body(
                strategy_id, profitGuard={"enabled": True, "givebackTiers": [
                    {"peakReturnPct": 0.10, "givebackPct": 0.30},
                    {"peakReturnPct": 0.25, "givebackPct": 0.40}]}))
            assert r.status_code == 422, r.text

            r2 = await client.post("/api/ai-trade/start", json=_start_body(
                strategy_id, profitGuard={"enabled": True, "atrFloorMult": 4.0,
                                          "atrCapMult": 1.0}))
            assert r2.status_code == 422, r2.text
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_profit_guard_adaptive_fields_are_mirrored_in_the_receipt(monkeypatch):
    """回执 `rule_wire` 镜像本轮五个新字段（阶梯 / ATR 上下界 / 冷却秒与范围），供 UI 与 config 复现。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_start_body(
                strategy_id, profitGuard={"enabled": True, "atrFloorMult": 0.5,
                                          "atrCapMult": 2.0, "cooldownSeconds": 240,
                                          "cooldownScope": "both",
                                          "givebackTiers": [
                                              {"peakReturnPct": 0.10, "givebackPct": 0.35}]}))
            assert r.status_code == 200, r.text
            guard = r.json()["profitGuard"]
            assert guard["atrFloorMult"] == 0.5 and guard["atrCapMult"] == 2.0
            assert guard["cooldownSeconds"] == 240.0 and guard["cooldownScope"] == "both"
            assert guard["givebackTiers"] == [{"peakReturnPct": 0.10, "givebackPct": 0.35}]
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


# ---------- 交易纪律预设（ai-trade-discipline-preset-and-integrity 轴 A/B；G-1 / G-14 / [P1-5]）----------
async def test_discipline_receipt_is_verifiable_end_to_end(monkeypatch):
    """请求不带 `discipline` ⇒ 服务端按默认预设装配，且回执**可逐字段核对**（绝不静默生效）。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_start_body(strategy_id))
            assert r.status_code == 200, r.text
            wire = r.json()["discipline"]
            assert wire["preset"] == "balanced" and wire["presetLabel"] == "均衡"
            assert wire["thresholds"]["ai_trade_sizing_enabled"] is True
            assert isinstance(wire["rows"], list) and "hint" in wire
            # 会话真的拿到了派生副本（不是只把数字写进回执）。
            group = get_ai_trade_manager()._group
            assert group.members[0].session.executor.settings.ai_trade_sizing_enabled is True
            assert group.sizing_rule is not None and group.stop_rule is not None
            # 单策略会话 ⇒ `balanced` 的共识门槛 1 恰好等于策略数，不夹紧、不产生调整项。
            assert wire["adjustments"] == []
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_start_receipt_carries_the_tradability_snapshot(monkeypatch):
    """轴 A（tradability-and-open-funnel §5.4）：启动回执在 `discipline` 同级带 `tradability`。

    桌面旧包 / 直接调 API 的用户拿不到弹窗那块预检，**回执是他们唯一的告知面**。
    两条纪律一并守：
    - **逐成员一份的列表**（R-10：分组级刻意不做任何合并——一个「平均否决率」既没有操作含义，
      AI-trade 也没有分组级实时通道可以可信地下发它）；
    - **探针失败绝不阻断启动**：本用例的 REST 桩不返回 K 线，`tradability` 应为空列表而不是 500。
    """
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_start_body(strategy_id))
            assert r.status_code == 200, r.text
            assert isinstance(r.json()["tradability"], list)
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_discipline_off_falls_back_to_the_pre_feature_behaviour(monkeypatch):
    """§7.1 零回归：显式 `off` ⇒ 三层全不装配、回执如实说「关闭」。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_start_body(
                strategy_id, discipline={"preset": "off"}))
            assert r.status_code == 200, r.text
            assert r.json()["discipline"]["preset"] == "off"
            assert r.json()["profitGuard"]["hardTakeProfitReturnPct"] == 0.0
            group = get_ai_trade_manager()._group
            assert group.members[0].session.executor.settings.ai_trade_sizing_enabled is False
            assert group.sizing_rule is None and group.stop_rule is None
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_conflicting_discipline_body_is_rejected_with_a_named_field(monkeypatch):
    """[P1-5]：「关闭」+ 覆盖字段 ⇒ 422 且点名字段——静默丢弃用户显式输入是明令禁止的形状。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_start_body(
                strategy_id, discipline={"enabled": False, "edgeGateK": 2.0}))
            assert r.status_code == 422, r.text
            error = r.json()["error"]
            assert error["code"] == "AI_TRADE_DISCIPLINE_CONFLICT"
            assert "edgeGateK" in error["message"]
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_small_priming_bars_are_raised_instead_of_rejecting_the_start(monkeypatch):
    """G-4：默认装配**绝不允许**把用户的启动打回 422；抬升在回执里如实披露。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_start_body(
                strategy_id, primingBars=60))
            assert r.status_code == 200, r.text
            raised = [a for a in r.json()["discipline"]["adjustments"]
                      if a["field"] == "primingBars"]
            assert len(raised) == 1 and raised[0]["from"] == 60 and raised[0]["to"] == 97
            assert get_ai_trade_manager()._group.members[0].session.priming_bars == 97
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_dust_risk_is_disclosed_in_the_receipt_but_never_blocks_the_start(monkeypatch):
    """[P0-4] 第一件（G-14）：预算按预设保守下界解算可能低于 minSz ⇒ **不 422**，如实写进回执。

    把「今天本来能启动」的用户挡在门外是更坏的回归；而不说，用户只会看到「AI 一直不开单」。
    """
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    # minSz 抬到让 f=1.0 的既有预检刚好放行、而 f=0.20 的复核必然命中的量级。
    # 预算 200 × 杠杆 3 ÷ (ctVal 0.01 × 价 30000) ⇒ f=1.0 时 2.0 张（既有预检放行），
    # f=0.20（balanced 的保守下界）时只有 0.4 张 ⇒ 复核必然命中。minSz 取两者之间。
    config = {**_CONFIG, "instMeta": {**_CONFIG["instMeta"], "minSz": 1.0}}
    _patch(monkeypatch, config=config)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_start_body(
                strategy_id, capitalUsd=200.0, leverage=3.0))
            assert r.status_code == 200, r.text
            notes = [a for a in r.json()["discipline"]["adjustments"]
                     if a["field"] == "volSizing"]
            assert len(notes) == 1 and "最小下单量" in notes[0]["reason"]
            # G-14 要的是**两个**落点：回执说了、纪律条也得说。只写回执，主面板上那一行仍是
            # 一片正常的绿，而现象与「AI 一直不开单」完全一致。本用例守这条线的前半段（复核结论
            # 真的随执行器走），后半段（它上到 `volSizing` 行）由 `test_ai_trade_discipline_session
            # .py::test_dust_risk_warns_on_the_vol_sizing_row_before_the_first_skip_happens` 守
            # ——本文件的会话是桩（`_FakeSession.discipline_wire` 恒空），digest 在这里验不到。
            assert get_ai_trade_manager()._group.members[0].session.executor.dust_risk is True
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_discipline_presets_endpoint_serves_labels_and_thresholds(monkeypatch):
    """弹窗的只读阈值表数据源：**不守 AI 总闸**（读一张表不下任何单），四档齐、标签为中文。"""
    await _fresh()
    app = _load_app()
    _override(app, ai=False, live=False)          # 总闸全关也必须能读
    try:
        async with await _client(app) as client:
            r = await client.get("/api/ai-trade/discipline-presets")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["defaultPreset"] == "balanced"
            assert [p["id"] for p in body["presets"]] == [
                "steady", "balanced", "aggressive", "off"]
            balanced = next(p for p in body["presets"] if p["id"] == "balanced")
            assert balanced["label"] == "均衡"
            keys = {row["key"] for row in balanced["rows"]}
            assert "ai_trade_edge_gate_k_min" in keys and "ai_trade_stop_atr_mult" in keys
            assert all(row["label"] and row["value"] for row in balanced["rows"])
            assert next(p for p in body["presets"] if p["id"] == "off")["rows"] == []
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()
