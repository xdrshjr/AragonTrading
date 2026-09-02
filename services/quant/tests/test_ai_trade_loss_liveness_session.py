"""亏损根因与活性加固的**会话 / API 级**用例（ai-trade-loss-and-liveness-hardening §八）。

纯核心与门禁在 `test_ai_trade_loss_liveness_core.py`；本文件测的是「判据取自哪里」——那正是本轮
评审判红三条 P0 的形态（新判据取自一个不携带该信息的入参，用例直接构造那个入参就恒绿）。故：

- `G-B7` 的断言**必须从 `HorizonRuntime.refresh()` 进**，喂真实 backstop wire，不许直接给
  `build_snapshot(backstop_covered=True)`；
- `G-A8` 的「start 422」与「冷续跑 200」**必须在同一个用例里**，否则下次有人把判据挪回装配链，
  两条分开的用例照样全绿；
- `G-D6` 必须在「模型每轮都自己写 `hold`、`degrade` 恒 `None`」的**真实事故形态**下判绿。
"""
from __future__ import annotations

import asyncio
import json

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.agent.llm.base import StreamEvent
from app.ai_auto import liveness as L
from app.ai_auto.base_rules import resolve_ma_band_rule
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.hold_horizon import RELEASE_LOSS, RELEASE_LOSS_COVERED, resolve_horizon_rule
from app.ai_auto.horizon_runtime import HorizonRuntime, build_horizon_facts
from app.ai_auto.manager import get_ai_trade_manager
from app.ai_auto.pause import PAUSE_REASON_LIVENESS
from app.ai_auto.profit_guard_runtime import SessionReadings
from app.ai_auto.session import AiTradeSession
from app.config import Settings, get_settings
from app.db import repositories as repo
from app.db.models import AppSetting, Strategy
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.live_auto.manager import get_live_auto_manager
from app.okx.accounts import AccountCredentials
from app.okx.types import Candle
from app.paper.strategy_factory import PaperStrategySpec
from app.strategies.base import BaseStrategy, StrategyMeta

_START_TS = 1_700_000_000
_STEP = 3600
_PX = 30_000.0
_CONFIG = {"instrumentSupported": True, "availUsdt": 1000.0, "posMode": "long_short_mode",
           "instMeta": {"ctVal": 0.01, "lotSz": 0.01, "minSz": 0.01, "tickSz": 0.1,
                        "maxLever": 125.0}, "lastPx": _PX}


# ============================ 轴 A：互锁只拦新建、不锁死续跑 ============================
class _FakeSession:
    """轻量会话桩（与 `test_ai_trade_api.py` 同款；本文件只关心端点的准入判据）。"""

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
        self.group_id = ""
        self.on_stopped = None
        self.on_liveness_halt = None
        self.pause_reason = None
        self.paused_ts = 0

    async def start(self):
        return None

    async def stop(self, reason, flatten=True):
        self.state = "stopped"
        return {"ok": True}

    @property
    def is_active(self) -> bool:
        return self.state in ("running", "paused")

    def info(self) -> dict:
        return {"sessionId": self.session_id, "runId": self.run_id, "instId": self.inst_id,
                "state": self.state}

    def discipline_wire(self) -> dict:
        return {"rows": [], "hint": None}

    def acceptance_bundle(self) -> dict:
        """`group_status()` 的验收三键取它；桩返回空 bundle ⇒ 三键为 None（不影响本文件的判据）。"""
        return {"acceptance": None, "exits": None, "funding": None, "acceptanceInput": None}

    def state_member_row(self) -> dict:
        """W2 契约行（启动时落一次盘）；桩只给身份字段。"""
        return {"instId": self.inst_id, "runId": self.run_id, "sessionId": self.session_id}


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
        return (await session.execute(
            select(Strategy.id).order_by(Strategy.id.asc()).limit(1))).scalar_one()


def _patch(monkeypatch) -> None:
    async def fake_config(*_a, **_k):
        return dict(_CONFIG)

    async def fake_empty(*_a, **_k):
        return []

    async def fake_resolve(*_a, **_k):
        return object()

    monkeypatch.setattr("app.ai_auto.manager.fetch_trading_config", fake_config)
    monkeypatch.setattr("app.ai_auto.manager.fetch_live_positions", fake_empty)
    monkeypatch.setattr("app.ai_auto.manager.fetch_live_open_orders", fake_empty)
    monkeypatch.setattr("app.ai_auto.manager.resolve_active_provider", fake_resolve)
    monkeypatch.setattr("app.ai_auto.manager.make_llm_client_from_config", lambda cfg: object())
    monkeypatch.setattr("app.ai_auto.manager.AiTradeSession", _FakeSession)


def _body(strategy_id: int, **over) -> dict:
    body = {"members": [{"instId": "BTC-USDT-SWAP", "allocationPct": 100}], "bar": "1H",
            "strategyIds": [strategy_id], "contextRefreshRounds": 10,
            # 致命组合：装了持仓周期锁（`intraday_swing` 的 min_hold = 2 小时）却关掉利润保护。
            "tradingHorizon": {"enabled": True, "profile": "intraday_swing"},
            "profitGuard": {"enabled": False}}
    body.update(over)
    return body


async def _seed_account(client) -> None:
    created = await client.post("/api/accounts", json={
        "label": "Dev-Fake", "apiKey": "ak-fake-0001",
        "secretKey": "sk-fake-secret-0001", "passphrase": "pp-fake-123456"})
    assert created.status_code == 201, created.text


async def test_g_a1_unguarded_horizon_is_rejected_with_exactly_three_remedies(monkeypatch):
    """`G-A1`：致命组合 ⇒ 422 `AI_TRADE_UNGUARDED_HORIZON`，且 `remedies` **恰好三条**。"""
    strategy_id = await _fresh()
    app = _load_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        live_trading_enabled=True, ai_trade_enabled=True)
    _patch(monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_body(strategy_id))
            assert r.status_code == 422, r.text
            err = r.json()["error"]
            assert err["code"] == "AI_TRADE_UNGUARDED_HORIZON"
            assert [m["id"] for m in err["remedies"]] == [
                "enable_profit_guard", "disable_horizon", "ops_override"]
            assert err["horizon"]["minHoldSeconds"] == 7200.0
            assert get_ai_trade_manager()._group is None, "422 之后绝不能留下一个半成品分组"
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_g_a2_master_switch_off_lets_the_same_body_through(monkeypatch):
    """`G-A2`：总闸关 ⇒ 同一份 body **200**（回退路径真的存在，不是一句承诺）。"""
    strategy_id = await _fresh()
    app = _load_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        live_trading_enabled=True, ai_trade_enabled=True,
        ai_trade_require_profit_exit_with_horizon=False)
    _patch(monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_body(strategy_id))
            assert r.status_code == 200, r.text
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_g_a3_interlock_does_not_widen_to_sessions_without_a_horizon_lock(monkeypatch):
    """`G-A3`：周期未装配 + 保护层关 ⇒ **200**（互锁只针对致命组合，不扩大打击面）。"""
    strategy_id = await _fresh()
    app = _load_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        live_trading_enabled=True, ai_trade_enabled=True)
    _patch(monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_body(
                strategy_id, tradingHorizon={"enabled": False}))
            assert r.status_code == 200, r.text
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_g_a8_cold_resume_of_a_fatal_combo_still_works(monkeypatch):
    """`G-A8`：**同一份 body**——`POST /ai-trade/start` 422，而冷续跑的重建链 200。

    两条断言必须在**同一个用例里**：分开写的话，下次有人把判据挪回 `build_start_request`
    （冷续跑复用它，且 `_rebuild_request` 把任何 HTTPException 一律翻成 409「启动契约已失效」），
    两条用例照样全绿——而生产里那 50 条「周期已装配 + 保护层未装配」的暂停会话会永久锁死，
    其中可能有正持有真实资金仓位的。
    """
    from app.ai_auto.resume import _rebuild_request

    strategy_id = await _fresh()
    app = _load_app()
    settings = Settings(live_trading_enabled=True, ai_trade_enabled=True)
    app.dependency_overrides[get_settings] = lambda: settings
    _patch(monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            await _seed_account(client)
            async with get_sessionmaker()() as db:
                account_id = (await repo.list_okx_accounts(db))[0].id
            body = _body(strategy_id, accountId=account_id)

            # ① 新启动被拦下
            started = await client.post("/api/ai-trade/start", json=body)
            assert started.status_code == 422, started.text

            # ② 同一份 `start_wire` 走冷续跑的重建链 ⇒ **正常返回**，绝不 409
            row = {"groupId": "g1", "createdTs": _START_TS, "resumedCount": 0,
                   "members": [], "startWire": body}
            req = await _rebuild_request(row, settings)
            assert req.horizon_rule is not None and req.profit_rule is None, (
                "重建出来的正是那个致命组合——互锁若在装配链里，上一行就已经抛 409 了")
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_g_a7_inert_is_the_same_value_in_config_json_and_group_status(monkeypatch):
    """`G-A7`：`config_json.discipline.inert` 与 `group_status().discipline.inert` **同值**。

    同一个装配点产出、两处只读转发；各算一份必然在下一次改判据时分叉。
    """
    strategy_id = await _fresh()
    app = _load_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        live_trading_enabled=True, ai_trade_enabled=True,
        ai_trade_require_profit_exit_with_horizon=False)
    _patch(monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_body(strategy_id))
            assert r.status_code == 200, r.text
            inert = r.json()["discipline"]["inert"]
            assert inert == ["ai_trade_profit_guard_hard_tp_return_pct"]
            run_id = r.json()["members"][0]["runId"]
            async with get_sessionmaker()() as db:
                run = await repo.get_run(db, run_id)
                cfg = json.loads(run.config_json)
            assert cfg["discipline"]["inert"] == inert
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


# ============================ 轴 B：覆盖态取自真实 wire ============================
class _BackstopStub:
    """鸭子类型的兜底核实替身：只提供 `wire(now_wall)` 与 `settings`（`HorizonRuntime` 只要这两样）。"""

    def __init__(self, wire: dict | None, settings) -> None:
        self._wire = wire
        self.settings = settings

    def wire(self, _now_wall: float) -> dict | None:
        return self._wire


class _Exec:
    """`build_horizon_facts` 只读 executor 的这几样；不起真会话即可从 `refresh()` 这一层进。"""

    class _Risk:
        max_hold_bars = None
        stop_loss_pct = 0.05

    projected = "long"
    entry_price = _PX
    leverage = 3.0
    entry_bar = 0
    risk = _Risk()
    trades: list = []
    entry_qty_sz = 1.0
    ct_val = 1.0

    def risk_trigger_px(self, side: str, fill: float):
        """`exchange_oco_px` 的只读转发对象；本文件不验 OCO，回一对 None 即可。"""
        return None, None


def _refresh_with(wire: dict | None, settings) -> dict:
    rule = resolve_horizon_rule({"profile": "intraday_swing"}, settings, bar="1H")
    runtime = HorizonRuntime(rule, backstop=_BackstopStub(wire, settings))
    readings = SessionReadings(seq=1, now_wall=float(_START_TS), mark_px=_PX * 0.7,
                              bar_index=1, px_age_s=1.0, atr_px=None)
    facts = build_horizon_facts(_Exec(), readings, stale_feed=False, cost_bps=10.0)
    return runtime.refresh(facts)


def test_g_b7_covered_comes_from_a_real_backstop_wire_not_a_bare_bool():
    """`G-B7`：断言**从 `HorizonRuntime.refresh()` 这一层进**。

    直接给 `build_snapshot(backstop_covered=True)` 的用例**不算数**——那正好绕过了 v1 出问题的
    那一段（判据读了一个 `classify_backstop` 从不产出的 `covered` 键，恒空转且不报错）。
    """
    settings = Settings()
    covered = _refresh_with({"basis": "exchange", "coverageRatio": 1.0}, settings)
    assert covered["releaseReason"] == RELEASE_LOSS_COVERED
    assert covered["releasedGates"] == ["G2", "G3"]
    # 本地口径 / 覆盖率未知 ⇒ 一律按**未覆盖**处置（fail-open，逐字回退今天的行为）
    for wire in ({"basis": "local", "coverageRatio": 1.0},
                 {"basis": "exchange", "coverageRatio": None},
                 {"basis": "exchange", "coverageRatio": 0.5},
                 None):
        snap = _refresh_with(wire, settings)
        assert snap["releaseReason"] == RELEASE_LOSS
        assert snap["releasedGates"] == ["G1", "G2", "G3"]


def test_g_b4_master_switch_off_returns_the_old_reason_verbatim():
    """`G-B4`：总闸关 ⇒ 恒 `loss_release`，与 `G-B2` 逐字节相同。"""
    off = Settings(ai_trade_loss_release_requires_uncovered=False)
    snap = _refresh_with({"basis": "exchange", "coverageRatio": 1.0}, off)
    assert snap["releaseReason"] == RELEASE_LOSS
    assert snap["releasedGates"] == ["G1", "G2", "G3"]


# ============================ 轴 D / E：会话级编排 ============================
class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_liveness", name="Stub", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


class _HoldLLM:
    """**真实事故形态的桩**：模型每一轮都自己写 `hold`（`degrade` 恒 `None`）。

    这正是生产实证里那 456 轮的形状——`openFunnel.intents` 只有 12、`degradedRounds` 只有 2。
    任何「数连续 N 轮同一降级码」的看门狗在这张数据上永远数不到 2（评审 P0-1）。
    """

    async def stream(self, *, system, messages, tools):
        if not tools:
            yield StreamEvent("text_delta", text="摘要")
            yield StreamEvent("message_done", stop_reason="end")
            return
        args = {"action": "hold", "reason": "通道算不出来，观望", "confidence": 0.6}
        yield StreamEvent("tool_use_start", id="t", name="submit_trade_decision")
        yield StreamEvent("tool_use_args_delta", id="t",
                          partial_json=json.dumps(args, ensure_ascii=False))
        yield StreamEvent("message_done", stop_reason="tool_use")


def _candle(idx: int, price: float = _PX) -> Candle:
    return Candle(ts=_START_TS + idx * _STEP, o=price, h=price + 5, l=price - 5,
                  c=price, vol=100.0, confirm=True)


def _make_session(settings: Settings, *, base_rule=None, profit_rule=None):
    async def place_order_fn(order):
        return {"ok": True, "ordId": "x", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        return {"ok": True, "clOrdId": "aragoncx", "closedSz": 0.1}

    async def positions_fn():
        return []

    async def fills_fn():
        return []

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(), ct_val=0.01,
        lot_sz=0.01, min_sz=0.01, settings=settings, place_order_fn=place_order_fn,
        close_position_fn=close_position_fn, positions_fn=positions_fn, fills_fn=fills_fn)
    decider = AiTradeDecider(_HoldLLM(), "sys", context_refresh_rounds=50,
                             settings=settings, reflection_enabled=False)
    spec = PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})
    session = AiTradeSession("aitrade_liveness", 1, [spec], "BTC-USDT-SWAP", "1H", executor,
                             decider, priming=False, priming_bars=10,
                             context_refresh_rounds=50, base_rule=base_rule,
                             profit_rule=profit_rule)

    async def no_htf(*_a, **_k):
        return []

    session._window._safe_get_candles = no_htf
    return session


async def _settle(session) -> None:
    task = session._decision_task
    if task is not None:
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _run_rounds(session, count: int) -> list[dict]:
    """喂 `count` 根确认根 ⇒ 跑 `count` 轮决策；窗口恒不足 21 根 ⇒ MA 通道永远算不出来。"""
    pushed: list[dict] = []
    session._push = lambda event: pushed.append(event) or asyncio.sleep(0)   # type: ignore
    for i in range(count):
        await session.feed_candle(_candle(i))
        await _settle(session)
    return pushed


async def test_g_d6_watchdog_halts_even_when_the_model_writes_hold_every_round():
    """`G-D6` —— **本轮最重要的一条回归护栏**（也是 v1 设计过不去的那一条）。

    喂一段恒不足 21 根的行情、且模型每一轮都自己写 `hold`（`degrade` 恒 `None`）。
    按「数 degrade 码」实现的看门狗在这张数据上必红；结构性探针必绿。
    """
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False, ai_trade_liveness_warn_rounds=2,
                        ai_trade_liveness_halt_rounds=4)
    base_rule = resolve_ma_band_rule({"enabled": True}, settings)
    session = _make_session(settings, base_rule=base_rule)
    halted: list[str] = []
    session.on_liveness_halt = lambda: halted.append("requested")
    try:
        pushed = await _run_rounds(session, 5)
        # 前提复现：模型确实每轮都自己写 hold，一道闸都没开火
        assert session.rounds_completed == 5
        assert session._liveness.state.code == L.BLOCK_MA_BAND
        assert session._liveness.state.streak == 5
        assert session._liveness.verdict()[0] == L.LEVEL_HALT
        assert halted == ["requested"], "停手请求必须**只投一次**（幂等）"
        # `G-D7`：halt 在决策轮**内部**只置意图，本轮照常跑完（零 CancelledError）
        assert session.state == "running", "会话绝不在决策轮里自己 cancel 自己"
        assert session._liveness_halt_pending is False, "轮末已投递，槽必须清空"
        levels = [e["data"]["level"] for e in pushed if e.get("type") == "liveness"]
        assert levels == ["degraded", "halt"], "只在档位**跃迁**时发事件（防刷屏）"
        # `G-D4`：halt 路径上一单未下、一仓未平
        assert session.executor.projected == "flat"
    finally:
        await session.stop("test")
        await dispose_engine()


async def test_g_d7_halt_is_delivered_only_after_the_round_has_finished():
    """`G-D7`：停手请求在**轮末**才交给 manager。

    `pause_group` 的第一步就是逐成员 `session.pause()`，而 `Session.pause` 的第 ③ 步是 cancel
    决策任务。若在判到 halt 的那一刻就投递，后台任务会在本轮的下一个 await 点把本轮 cancel 掉，
    这一轮的 trace 与决策事件就此丢失——那是「自己 cancel 自己」的一个更隐蔽的变体。
    """
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False, ai_trade_liveness_warn_rounds=1,
                        ai_trade_liveness_halt_rounds=1)
    session = _make_session(settings, base_rule=resolve_ma_band_rule({"enabled": True}, settings))
    seen: list[int] = []
    # 回调触发的那一刻，本轮必须**已经**跑完（`rounds_completed` 已自增）
    session.on_liveness_halt = lambda: seen.append(session.rounds_completed)
    try:
        await _run_rounds(session, 1)
        assert seen == [1], "回调必须在 `rounds_completed` 自增之后才被调到"
    finally:
        await session.stop("test")
        await dispose_engine()


async def test_g_d2_a_single_fill_clears_the_streak():
    """`G-D2`：任意一次成交 ⇒ `streak` 归零（系统证明了自己还活着）。"""
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    session = _make_session(settings, base_rule=resolve_ma_band_rule({"enabled": True}, settings))
    try:
        await _run_rounds(session, 3)
        assert session._liveness.state.streak == 3
        await session._dispatch([{"type": "trade", "data": {
            "ts": _START_TS, "side": "long", "price": _PX, "qty": 0.01, "fee": 0.0,
            "pnl": 0.0, "positionAfter": "long"}}])
        assert session._liveness.state.streak == 0
    finally:
        await session.stop("test")
        await dispose_engine()


async def test_g_d5_master_switch_off_keeps_the_old_alert_verbatim():
    """`G-D5`：总闸关 ⇒ `liveness` 键**缺席**、不发该事件；第 3 轮那条既有 error **逐字节照发**。"""
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False, ai_trade_liveness_enabled=False)
    session = _make_session(settings, base_rule=resolve_ma_band_rule({"enabled": True}, settings))
    try:
        pushed = await _run_rounds(session, 4)
        assert "liveness" not in session.info()
        assert not [e for e in pushed if e.get("type") == "liveness"]
        errors = [e for e in pushed
                  if e.get("type") == "error"
                  and e["data"].get("code") == "AI_TRADE_BASE_RULE_UNAVAILABLE"]
        assert len(errors) == 1, "既有的第 3 轮告警必须照发且**只发一次**"
        assert "已连续 3 轮" in errors[0]["data"]["message"]
        assert session._band_unavailable_streak == 4, "band 计数在总闸关时照旧推进"
    finally:
        await session.stop("test")
        await dispose_engine()


async def test_g_d8_halt_goes_through_pause_group_and_lands_on_disk(monkeypatch):
    """`G-D8`：停手走的是 **`manager.pause_group(..., flatten=False)`**，不是 `session.pause`。

    绕过 `pause_group` ⇒ 磁盘上这一组仍是 `running`，进程重启后会被复活成「运行中」——
    而它恰恰是刚刚被判定为「已死」的那个会话（评审 P0-4 ②）。
    """
    manager = get_ai_trade_manager()
    calls: list[tuple] = []

    async def fake_pause(group_id, *, reason="user", flatten=False):
        calls.append((group_id, reason, flatten))
        return {"groupId": group_id}

    monkeypatch.setattr(manager, "pause_group", fake_pause)
    group = type("_G", (), {"group_id": "g-halt"})()
    manager._group = group
    try:
        manager._request_liveness_pause(group)
        for _ in range(20):        # 后台任务是 fire-and-forget，让出事件循环等它跑完
            if calls:
                break
            await asyncio.sleep(0)
        assert calls == [("g-halt", PAUSE_REASON_LIVENESS, False)]
    finally:
        manager._group = None


async def test_liveness_pause_task_is_drained_before_shutdown_touches_the_db():
    """关停链：`shutdown()` 先收掉看门狗投出的停手任务，再往下走。

    那条任务做的事是 `pause_group` ⇒ 它会写库（`_persist_group_state`），而关停步骤表再往后几步
    就是 `db_engine` 的 `dispose_engine`——一个活着穿过引擎释放的写任务会当场抛（经验层 ★ E-4 的
    同款教训）。**关停步骤表一步都不加**：收敛落在既有的 `ai_trade` 那一步内部。
    """
    manager = get_ai_trade_manager()
    manager._group = None
    finished: list[str] = []

    async def slow() -> None:
        await asyncio.sleep(0)
        finished.append("done")

    task = asyncio.create_task(slow())
    manager._liveness_tasks.add(task)
    task.add_done_callback(manager._liveness_tasks.discard)
    try:
        result = await manager.shutdown()
        assert finished == ["done"], "shutdown 必须等它跑完，而不是把它丢在地上"
        assert result["action"] == "noop"          # 无分组时的既有回执逐字不变
    finally:
        manager._liveness_tasks.clear()


async def test_liveness_halt_request_is_dropped_after_the_group_changed():
    """身份守卫：分组已换代 / 已清空时，这条停手请求属于上一个分组 ⇒ 丢弃（不误停新分组）。"""
    manager = get_ai_trade_manager()
    manager._group = None
    stale = type("_G", (), {"group_id": "g-old"})()
    manager._request_liveness_pause(stale)          # 不抛、不排任何任务
    assert not manager._liveness_tasks


# ============================ 轴 E：归因四键与漏斗末格 ============================
async def test_g_e7_horizon_gate_verdict_lands_on_the_close_trade():
    """`G-E7`：违反最短持仓的平仓，其 `explain.meta` 带四键——「为什么能平掉」在产品里可直接回答。

    另断言 `holdSeconds` **仍由既有写点产出**（`trip_ledger`），本轮绝不写第二份。
    """
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    rule = resolve_horizon_rule({"profile": "intraday_swing"}, settings, bar="1H")
    session = _make_session(settings)
    session.horizon_rule = rule
    session._horizon = HorizonRuntime(rule)
    try:
        # `gate_status` 读的是 `released` 这个布尔键（不是 `status`），照 `build_snapshot` 的形状给。
        session._round_horizon = {"status": "released", "released": True,
                                  "releaseReason": RELEASE_LOSS_COVERED}
        session._note_exit_priority_round()
        trade = {"ts": _START_TS, "side": "close", "price": _PX, "qty": 0.01, "fee": 0.0,
                 "pnl": -1.0, "positionAfter": "flat",
                 "explain": {"kind": "atomic", "side": "close", "headline": "AI 决策 · 平仓"}}
        session._inject_explain_meta(trade)
        meta = trade["explain"]["meta"]
        assert meta["horizonGate"] == "released"
        assert meta["horizonRelease"] == RELEASE_LOSS_COVERED
        assert meta["minHoldSeconds"] == rule.min_hold_s
        assert "exitPriorityLevel" in meta
        assert "holdSeconds" not in meta, "holdSeconds 已有唯一写点（trip_ledger），不得再写一份"
        # **消费即清空**：下一笔任意平仓绝不继承本轮的判定
        second = {"ts": _START_TS + 1, "side": "close", "price": _PX, "qty": 0.01,
                  "fee": 0.0, "pnl": 0.0, "positionAfter": "flat",
                  "explain": {"kind": "profit_guard", "side": "close", "headline": "保护平仓"}}
        session._inject_explain_meta(second)
        assert "horizonGate" not in second["explain"]["meta"]
    finally:
        await session.stop("test")
        await dispose_engine()


async def test_e5_keys_are_absent_when_the_horizon_is_not_armed():
    """周期模式未装配 ⇒ 四键**整键缺席**（老行同样缺键 = 「未记录」，绝不填默认值）。"""
    await init_db()
    session = _make_session(Settings(ai_trade_funding_enabled=False))
    try:
        session._note_exit_priority_round()
        trade = {"ts": _START_TS, "side": "close", "price": _PX, "qty": 0.01, "fee": 0.0,
                 "pnl": 0.0, "positionAfter": "flat",
                 "explain": {"kind": "atomic", "side": "close", "headline": "h"}}
        session._inject_explain_meta(trade)
        for key in ("horizonGate", "horizonRelease", "minHoldSeconds", "exitPriorityLevel"):
            assert key not in trade["explain"]["meta"]
    finally:
        await session.stop("test")
        await dispose_engine()


async def test_g_e9_cancelled_limit_open_lands_in_the_funnel_by_its_own_side():
    """`G-E9`：限价开仓撤单 ⇒ 漏斗 `expired += 1` 且 `filled` 不变；方向取**委托腿自己的 side**。

    撤单常在数轮之后才到达，那时轮级 `_intent_side` 早已被 `note_executed` 清零——照轮级标志读
    会让 `expiredBySide` 恒 0 且不报任何错（`note_filled` 文档注释里那个坑）。
    """
    await init_db()
    session = _make_session(Settings(ai_trade_funding_enabled=False))
    funnel = session._discipline.funnel
    try:
        session.executor.open_expired_by_side = {"short": 2}
        await session._dispatch([])          # `_dispatch` 是全部 executor 事件的唯一收口
        assert funnel.expired == 2 and funnel.filled == 0
        assert funnel.expired_by_side == {"short": 2}
        await session._dispatch([])          # 差量同步：不重复计数
        assert funnel.expired == 2
        from app.ai_auto.open_funnel import funnel_wire
        wire = funnel_wire(funnel)
        assert wire["expired"] == 2 and wire["insufficientBalance"] == 0
    finally:
        await session.stop("test")
        await dispose_engine()


async def test_g_e10_insufficient_balance_is_observed_and_changes_no_size():
    """`G-E10`（默认档）：`INSUFFICIENT_BALANCE` 进漏斗，**提交张数逐字不变**（本轮只做观测）。"""
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    assert settings.ai_trade_sizing_margin_precheck is False, "降档行为本轮默认关"
    session = _make_session(settings)
    try:
        await session._dispatch([{"type": "error", "data": {
            "ts": _START_TS, "code": "INSUFFICIENT_BALANCE", "message": "可用余额不足"}}])
        assert session._discipline.funnel.insufficient_balance == 1
        # 其它错误码一律不进这一格
        await session._dispatch([{"type": "error", "data": {
            "ts": _START_TS, "code": "ORDER_NOT_FOUND", "message": "x"}}])
        assert session._discipline.funnel.insufficient_balance == 1
    finally:
        await session.stop("test")
        await dispose_engine()


async def test_g_e4_peak_pnl_survives_a_session_without_the_profit_guard():
    """`G-E4`：保护层未装配 ⇒ 峰值浮盈仍被追踪（实测 82.9% 为 NULL 的根因就在这里）。

    关掉保护层的同时也关掉「保护层本该拦住多少钱」这个读数，恰恰关掉了唯一能证明该开它的证据。
    总闸 `ai_trade_peak_tracking_always=False` ⇒ 恢复 NULL。
    """
    from app.ai_auto.profit_guard_runtime import ProfitGuardRuntime, build_position_facts

    readings = SessionReadings(seq=1, now_wall=float(_START_TS), mark_px=_PX * 1.2,
                               bar_index=1, px_age_s=1.0, atr_px=None)
    facts = build_position_facts(_Exec(), readings)

    armed = ProfitGuardRuntime(None, peak_tracking_always=True)
    assert armed.refresh(facts, stale_feed=False) is None, "未装配 ⇒ 快照恒 None（prompt 逐字回退）"
    assert armed.take_peak_pnl() is not None

    off = ProfitGuardRuntime(None, peak_tracking_always=False)
    assert off.refresh(facts, stale_feed=False) is None
    assert off.take_peak_pnl() is None


def test_g_e5_peak_is_none_not_zero_when_the_snapshot_is_unavailable():
    """`G-E5`：一路 unavailable ⇒ 峰值仍为 `None`，**绝不为 0**（0 会被捕获率聚合当成真读数）。"""
    from app.ai_auto.profit_guard_runtime import ProfitGuardRuntime, build_position_facts

    readings = SessionReadings(seq=1, now_wall=float(_START_TS), mark_px=_PX,
                               bar_index=1, px_age_s=1.0, atr_px=None)
    runtime = ProfitGuardRuntime(None, peak_tracking_always=True)
    runtime.refresh(build_position_facts(_Exec(), readings), stale_feed=True)   # 行情降级
    assert runtime.take_peak_pnl() is None


# ============================ 轴 C-2：跳轮的会话侧接线 ============================
async def test_c2_skip_round_costs_no_llm_call_and_does_not_inflate_rounds():
    """C-2：结构上不可能产生任何合法动作的轮次**整轮跳过**（连 payload 都不建）。

    两条断言缺一不可：
    ① `rounds_completed` **刻意不含跳过的轮**——那一轮没做过任何决策，算进轮数会污染
       「N 轮 0 成交」这个度量本身，而那正是本轮要修的活性问题的度量；
    ② 总闸默认关 ⇒ 本段整体不执行 ⇒ 逐字节回退。
    """
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False, ai_trade_skip_round_when_gated=True)
    rule = resolve_horizon_rule({"profile": "intraday_swing"}, settings, bar="1H")
    session = _make_session(settings)
    session.horizon_rule = rule
    session._horizon = HorizonRuntime(rule)
    pushed: list[dict] = []
    session._push = lambda event: pushed.append(event) or asyncio.sleep(0)   # type: ignore
    try:
        # 造一个「持仓中 + 一道闸都没解 + 最短持仓还剩 15 分钟」的断面
        session._horizon.refresh = lambda _facts: {           # type: ignore[method-assign]
            "status": "holding", "releasedGates": [], "minHoldRemainingSeconds": 900.0}
        wake = {"kind": "heartbeat", "trigger": "interval"}
        assert await session._maybe_skip_round(7, _START_TS, wake) is True
        assert session.rounds_skipped == 1
        assert session.rounds_completed == 0, "跳过的轮**不计入**决策轮数"
        evt = [e for e in pushed if e.get("type") == "round_skipped"]
        assert len(evt) == 1
        assert evt[0]["data"]["reason"] == "horizon_locked"
        assert evt[0]["data"]["roundsSkipped"] == 1, "计数随事件下发（前端不自增第二份）"
        # 安全内核：外部事件驱动的唤醒一律不跳
        assert await session._maybe_skip_round(
            8, _START_TS, {"kind": "intel_alert", "trigger": "interval"}) is False
    finally:
        await session.stop("test")
        await dispose_engine()


async def test_c2_master_switch_off_never_skips():
    """总闸默认关 ⇒ 本段整体不执行（连断面都不算一次）。"""
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    assert settings.ai_trade_skip_round_when_gated is False
    rule = resolve_horizon_rule({"profile": "intraday_swing"}, settings, bar="1H")
    session = _make_session(settings)
    session.horizon_rule = rule
    session._horizon = HorizonRuntime(rule)
    try:
        session._horizon.refresh = lambda _facts: {           # type: ignore[method-assign]
            "status": "holding", "releasedGates": [], "minHoldRemainingSeconds": 900.0}
        assert await session._maybe_skip_round(
            1, _START_TS, {"kind": "heartbeat", "trigger": "interval"}) is False
        assert session.rounds_skipped == 0
    finally:
        await session.stop("test")
        await dispose_engine()


async def test_e8_margin_precheck_knob_is_disclosed_in_the_start_receipt(monkeypatch):
    """E-8：开着那把「本轮不实现」的总闸 ⇒ 启动回执里如实多出一条说明（唯一读点）。"""
    strategy_id = await _fresh()
    app = _load_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        live_trading_enabled=True, ai_trade_enabled=True,
        ai_trade_require_profit_exit_with_horizon=False,
        ai_trade_sizing_margin_precheck=True)
    _patch(monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            await _seed_account(client)
            r = await client.post("/api/ai-trade/start", json=_body(strategy_id))
            assert r.status_code == 200, r.text
            fields = [a["field"] for a in r.json()["discipline"]["adjustments"]]
            assert "sizingMarginPrecheck" in fields
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await dispose_engine()


# ============================ A1：11 把总闸全关时的零回归门禁 ============================
async def test_a1_every_new_switch_off_adds_nothing_to_payload_status_or_events():
    """`A1`（§8.1）的**可执行**形态。

    原文写的是「payload / prompt / decision / snapshot 逐字节等于 `5337da1`」——那需要一份 golden
    快照，而 golden 会在下一轮任何一次合法改动上变红，且红了也判断不出该不该红。故本用例断言它
    真正要保的那件事：**本轮的 11 把总闸全部置回退档时，决策 payload、两条 status 通路与 WS 事件
    流里不出现任何一个本轮新增的键**。

    `A1b` 的六处**有意的、无闸的**加法（E-1/E-2/E-4/E-5/E-6/E-7 的数据完整性修复）刻意不在本断言
    范围内——它们各有专属兼容性用例（`G-E1`..`G-E11`）。把它们也钉进来，等于把「有意的加法」
    冤枉成回归，下一次有人为了让本用例变绿而去删掉一个本该存在的键。
    """
    await init_db()
    settings = Settings(
        ai_trade_funding_enabled=False,
        ai_trade_require_profit_exit_with_horizon=False,   # 轴 A
        ai_trade_loss_release_requires_uncovered=False,    # 轴 B
        ai_trade_liveness_enabled=False,                   # 轴 D
        ai_trade_wake_horizon_divisor=0,                   # 轴 C-1（默认档即回退档）
        ai_trade_skip_round_when_gated=False,              # 轴 C-2
        ai_trade_peak_tracking_always=False,               # E-3
        ai_trade_exit_verdict_min_samples=0,               # 轴 F
        ai_trade_exit_discretion_autotune_enabled=False,   # 轴 F（本轮只建不开）
        ai_trade_sizing_margin_precheck=False,             # E-8
    )
    rule = resolve_horizon_rule({"profile": "intraday_swing"}, settings, bar="1H")
    session = _make_session(settings, base_rule=resolve_ma_band_rule({"enabled": True}, settings))
    session.horizon_rule = rule
    session._horizon = HorizonRuntime(rule)
    try:
        # 跑几轮「通道恒算不出来」的真实事故形态：这正是各轴最容易误触发的那种输入。
        pushed = await _run_rounds(session, 3)
        assert not [e for e in pushed if e.get("type") in ("liveness", "round_skipped")], \
            "总闸全关时绝不允许多出本轮的任何一种新事件"
        payload = session._build_decision_payload(
            4, _START_TS, _PX, [], {"kind": "heartbeat", "trigger": "interval"})
        blob = json.dumps(payload, ensure_ascii=False, default=str)
        for token in ("liveness", RELEASE_LOSS_COVERED, "exitDiscretion", "roundsSkipped"):
            assert token not in blob, f"总闸全关时 payload 里出现了本轮新增的 `{token}`"
        for wire in (session.info(), session._status_payload()):
            assert "liveness" not in wire, "总闸关 ⇒ 整键**缺席**（不是 null）"
        assert session.rounds_skipped == 0
        # E-3 的回退档：峰值追踪跟着保护层一起不装配 ⇒ 逐字回到今天的「恒 NULL」
        assert session._guard.take_peak_pnl() is None
    finally:
        await session.stop("test")
        await dispose_engine()
