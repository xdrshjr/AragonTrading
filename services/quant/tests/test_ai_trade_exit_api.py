"""【立即离场】端点的 REST 契约（ai-trade-exit-priority-and-commitment §6.2 · #33）。

守门极性是本文件的重点：`/exit` 是**收敛风险**的动作，故与 `/stop`、`/backstop/repair` 同理
**不守 AI 总闸**（总闸关闭时仍 200）；它守的是运维总闸 `ai_trade_manual_exit_enabled`（关 ⇒ 409）。

另一条重点是【R:P1-6】：`/exit` 是**会话继续运行**的中途动作，一次失败**绝不能**置
`flatten_failed` / `residual`——那两个字段被面板读成终态红条且全仓没有任何复位点，一次中途失败会
让此后每一次 status 都宣称「AI 自动交易停止时平仓失败」，即便下一轮 Agent 自己已经把仓平掉了。

OKX / LLM / 会话全程打桩；铁律：占位假凭证，绝不写真实凭证。
"""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.ai_auto.manager import get_ai_trade_manager
from app.ai_auto.session import AiTradeManualExitDisabled
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
    """会话桩：`exit_now` 的三种结局由类级旋钮切换（成功 / 失败 / 运维总闸关）。"""

    outcome: str = "ok"
    exits: list[str] = []

    def __init__(self, session_id, run_id, specs, inst_id, bar, executor, decider, **kw):
        self.session_id = session_id
        self.run_id = run_id
        self.executor = executor
        self.inst_id = inst_id
        self.bar = bar
        self.state = "running"
        self.stop_reason = None
        self.pause_reason = None
        self.paused_ts = 0
        self.group_id = ""
        self.on_stopped = None
        self.flatten_failed = False        # 【R:P1-6】本文件的核心断言就落在它上面
        self.residual = None
        self.strategy_ids = [s.strategy_id for s in specs]
        self.strategy_names = [s.strategy_name for s in specs]
        self.context_refresh_rounds = kw.get("context_refresh_rounds", 10)
        self.take_profit_usd = kw.get("take_profit_usd")
        self.stop_loss_usd = kw.get("stop_loss_usd")

    async def start(self):
        return None

    async def stop(self, reason="user_stop", *, flatten=True):
        self.state = "stopped"
        self.stop_reason = reason
        sm = get_sessionmaker()
        async with sm() as s:
            await repo.finalize_run(s, self.run_id, status="stopped", end_ts=_START_TS)
        if self.on_stopped is not None:
            await self.on_stopped(self)
        return {"ok": True, "closedSz": 0}

    @property
    def is_active(self):
        return self.state in ("running", "paused")

    async def exit_now(self):
        _FakeSession.exits.append(self.inst_id)
        if _FakeSession.outcome == "disabled":
            raise AiTradeManualExitDisabled()
        if _FakeSession.outcome == "failed":
            # 会话侧的真实语义：失败**不置**终态字段，只推一条独立的 error（见 §6.2）。
            return {"ok": False, "instId": self.inst_id, "groupId": self.group_id,
                    "flattened": {"ok": False, "message": "平仓未成交"},
                    "positionAfter": "long", "restingCancelled": False,
                    "message": "立即离场未成功"}
        return {"ok": True, "instId": self.inst_id, "groupId": self.group_id,
                "flattened": {"ok": True, "closedSz": 0.1}, "positionAfter": "flat",
                "restingCancelled": True, "message": "已按市价平掉本会话自管持仓，会话继续运行"}

    def state_member_row(self):
        return {"instId": self.inst_id, "runId": self.run_id, "sessionId": self.session_id,
                "position": None, "positionUnknown": False, "restingClOrdId": None,
                "knownClOrdIds": []}

    def discipline_wire(self):
        return {"rows": [], "hint": None}

    def acceptance_bundle(self):
        return {"acceptance": None, "exits": None, "funding": None, "acceptanceInput": None}

    def info(self):
        return {"sessionId": self.session_id, "runId": self.run_id, "state": self.state,
                "pauseReason": self.pause_reason, "instId": self.inst_id, "bar": self.bar,
                "strategyIds": self.strategy_ids, "strategyNames": self.strategy_names,
                "budgetUsd": self.executor.budget_usd, "leverage": self.executor.leverage,
                "takeProfitUsd": self.take_profit_usd, "stopLossUsd": self.stop_loss_usd,
                "contextRefreshRounds": self.context_refresh_rounds, "roundsCompleted": 0,
                "roundsSinceSummary": 0, "positionSide": "flat", "sessionPnlUsd": 0.0,
                "startedTs": _START_TS, "lastDecision": None, "contextSummary": None,
                "degraded": False, "flattenFailed": self.flatten_failed}


def _load_app():
    from app.main import app
    return app


async def _fresh() -> int:
    await init_db()
    manager = get_ai_trade_manager()
    manager._group = None
    manager._paused_ref = None
    manager._hydration_failed = False
    get_live_auto_manager()._group = None
    _FakeSession.outcome = "ok"
    _FakeSession.exits = []
    sm = get_sessionmaker()
    async with sm() as session:
        for row in await repo.list_okx_accounts(session):
            await session.delete(row)
        await session.execute(delete(AppSetting))
        await session.commit()
        return (await session.execute(
            select(Strategy.id).order_by(Strategy.id.asc()).limit(1))).scalar_one()


def _override(app, *, ai: bool = True, live: bool = True, **extra) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        live_trading_enabled=live, ai_trade_enabled=ai, **extra)


def _patch(monkeypatch) -> None:
    async def fake_config(*_a, **_k):
        return dict(_CONFIG)

    async def empty(*_a, **_k):
        return []

    async def fake_resolve(*_a, **_k):
        return object()

    async def no_fee_rate(*_a, **_k):
        return None

    monkeypatch.setattr("app.ai_auto.manager.fetch_trading_config", fake_config)
    monkeypatch.setattr("app.ai_auto.manager.fetch_live_positions", empty)
    monkeypatch.setattr("app.ai_auto.manager.fetch_live_open_orders", empty)
    # 轴 A-2/A-5 之后启动期走 `fetch_trade_fee_ex`（它多带一份「每一跳为什么失败」）。
    # 替身返回 `None`，由 `fee_rates.as_outcome` 归一成「没取到、也没有原因可讲」。
    monkeypatch.setattr("app.ai_auto.manager.fetch_trade_fee_ex", no_fee_rate)
    monkeypatch.setattr("app.ai_auto.manager.resolve_active_provider", fake_resolve)
    monkeypatch.setattr("app.ai_auto.manager.make_llm_client_from_config", lambda cfg: object())
    monkeypatch.setattr("app.ai_auto.manager.AiTradeSession", _FakeSession)


async def _start_group(client, strategy_id: int) -> str:
    created = await client.post("/api/accounts", json={
        "label": "Dev-Fake", "apiKey": "ak-fake-0001",
        "secretKey": "sk-fake-secret-0001", "passphrase": "pp-fake-123456"})
    assert created.status_code == 201, created.text
    started = await client.post("/api/ai-trade/start", json={
        "members": [{"instId": "BTC-USDT-SWAP", "allocationPct": 100}], "bar": "1H",
        "strategyIds": [strategy_id], "contextRefreshRounds": 10})
    assert started.status_code == 200, started.text
    return started.json()["groupId"]


def _url(group_id: str) -> str:
    return f"/api/ai-trade/{group_id}/member/BTC-USDT-SWAP/exit"


# ---------------- 主链路 ----------------
async def test_exit_returns_200_and_leaves_the_session_running(monkeypatch):
    """A-6：平仓成功后会话**仍在运行**（`/exit` 不是 `/stop`）。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            group_id = await _start_group(client, strategy_id)
            res = await client.post(_url(group_id))
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["ok"] is True and body["positionAfter"] == "flat"
            assert body["instId"] == "BTC-USDT-SWAP"
            status = await client.get("/api/ai-trade/status")
            assert status.json()["group"]["state"] == "running"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_exit_does_not_guard_the_ai_master_switch(monkeypatch):
    """**收敛动作恒可用**：AI 总闸关闭时 `/exit` 仍 200（与 `/stop`、`/backstop/repair` 同理）。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            group_id = await _start_group(client, strategy_id)
            _override(app, ai=False)          # 起完组之后再关总闸
            res = await client.post(_url(group_id))
            assert res.status_code == 200, res.text
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_ops_switch_off_returns_409(monkeypatch):
    """运维总闸关 ⇒ 409 `AI_TRADE_MANUAL_EXIT_DISABLED`（端点层是唯一的翻译点）。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            group_id = await _start_group(client, strategy_id)
            _FakeSession.outcome = "disabled"
            res = await client.post(_url(group_id))
            assert res.status_code == 409, res.text
            # `main.http_exception_handler` 把 dict detail 统一包成 `{"error": {...}}`
            assert res.json()["error"]["code"] == "AI_TRADE_MANUAL_EXIT_DISABLED"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


def test_exchange_failure_text_puts_the_chinese_reason_first():
    """交易所业务失败的一句人话：**中文原因在前**，原始 sCode 括在后面。

    §6.2 原本要求把它映射成 502，但 `executor.flatten` **不抛** `OKXError`（内部重试后返回
    结构化失败），照字面写会得到一段不可达的死代码——见 §十一 D-10。失败因此沿
    200 + `{"ok": false, "message"}` 返回，而 `message` 必须自解释。
    """
    from app.ai_auto.session import _manual_exit_failure_text

    text = _manual_exit_failure_text(
        {"ok": False, "code": "51023", "message": "Position does not exist"})
    assert text.startswith("立即离场未成功：")
    assert "51023" in text
    assert text.index("立即离场未成功") < text.index("51023")   # 中文在前
    # 无 sCode / 无原文时不留空括号、不编一个原因
    assert _manual_exit_failure_text({"ok": False}) == "立即离场未成功"


async def test_missing_group_or_member_returns_404(monkeypatch):
    """分组 / 成员缺失 ⇒ 404（镜像 `stop_member`）。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            assert (await client.post(_url("no-such-group"))).status_code == 404
            group_id = await _start_group(client, strategy_id)
            res = await client.post(f"/api/ai-trade/{group_id}/member/ETH-USDT-SWAP/exit")
            assert res.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


# ---------------- 【R:P1-6】失败不留终态红条 ----------------
async def test_a_failed_exit_never_sets_the_terminal_flatten_flag(monkeypatch):
    """A-6 追加：`/exit` **失败**后会话仍在运行，且 `status.flattenFailed` 保持 `False`。

    `flatten_failed` / `residual` 全仓**没有任何复位点**（只在 `__init__` 置 `False`），一次中途
    失败会让此后每一次 status 都宣称「AI 自动交易停止时平仓失败，请立即到下方实盘面板手动平仓」
    ——即便下一轮 Agent 自己已经把仓平掉了。
    """
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            group_id = await _start_group(client, strategy_id)
            _FakeSession.outcome = "failed"
            res = await client.post(_url(group_id))
            assert res.status_code == 200 and res.json()["ok"] is False
            status = await client.get("/api/ai-trade/status")
            group = status.json()["group"]
            assert group["state"] == "running"
            # 成员行 = 会话 `info()` 全字段（`flattenFailed` 是它的末位键）
            assert group["members"][0]["flattenFailed"] is False
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


# ---------------- horizon-profiles 的只读块 ----------------
async def test_horizon_profiles_carries_the_exit_priority_block(monkeypatch):
    """§6.2：档位标签、配额、地板与那句安全承诺**全部由后端下发**（前端一个字都不硬编）。"""
    await _fresh()
    app = _load_app()
    _override(app)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            block = (await client.get("/api/ai-trade/horizon-profiles?bar=1H")
                     ).json()["exitPriority"]
            assert block["enabled"] is True
            assert [level["id"] for level in block["levels"]] == ["high", "final"]
            assert "只放行平仓" in block["note"]       # X-1 在 UI 上的唯一落点
            _override(app, ai_trade_exit_priority_enabled=False)
            off = (await client.get("/api/ai-trade/horizon-profiles?bar=1H")).json()
            assert off["exitPriority"] is None        # 总闸关 ⇒ 前端整块不渲染
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()
