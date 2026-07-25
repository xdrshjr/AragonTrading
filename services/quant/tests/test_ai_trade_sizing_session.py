"""仓位 / 刹车层的会话级集成测试（vol-sizing-and-drawdown-brake §6.4 / T-4/T-9/T-17b/T-17c/T-25/T-29）。

脱网、桩回调、桩 LLM。守住四件在纯函数单测里看不见的事：
1. **T-4 零回归**：全部开关取默认（关闭）值 ⇒ payload 无 `sizingPlan` 键、trace 零 `sizing` 事件、
   `executor.risk` **对象 `is` 相等**（从不被赋值）；
2. **S-11（T-17b/T-17c）**：有挂单 / 有持仓时 `executor.risk` 一行不动，`stopSource` 如实落三态；
3. **S-14（T-25）**：走 `_adopt_fill` 路径的成交事件一经 `_dispatch` 就能被敞口簿看见，不必等下一轮决策；
4. **T-29**：`k_min=0`（默认）时凯利项仍可算——总闸①绝不隐含依赖另一层会真实否决开仓的资金门禁。
"""
from __future__ import annotations

import asyncio
import json

from app.agent.llm.base import StreamEvent
from app.ai_auto.decider import AiTradeDecider, TradeDecision
from app.ai_auto.exposure_book import GroupExposureBook
from app.ai_auto.manager import (
    AiTradeGroupMemberInput,
    AiTradeGroupStartRequest,
    AiTradeManager,
    AiTradePreflightError,
)
from app.ai_auto.session import AiTradeSession
from app.ai_auto.sizing_runtime import (
    SizingContext,
    SizingRuntime,
    apply_risk_override,
    build_sizing_runtime,
    can_override_risk,
    prior_p_win,
)
from app.ai_auto.sizing import StopPlan
from app.config import Settings
from app.db.session import dispose_engine, init_db
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.live_auto.resting import RestingOrder
from app.okx.accounts import AccountCredentials
from app.okx.types import Candle
from app.paper.strategy_factory import PaperStrategySpec
from app.strategies.base import BaseStrategy, StrategyMeta

_START_TS = 1_700_000_000
_STEP = 3600
_PX = 30_000.0


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_sizing", name="Stub", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


class _StubLLM:
    def __init__(self, action="open_long", size=1.0):
        self.action = action
        self.size = size

    async def stream(self, *, system, messages, tools):
        if tools:
            args = {"action": self.action, "reason": "r", "confidence": 0.7}
            if self.action in ("open_long", "open_short"):
                args["sizeFraction"] = self.size
            yield StreamEvent("tool_use_start", id="t", name="submit_trade_decision")
            yield StreamEvent("tool_use_args_delta", id="t",
                              partial_json=json.dumps(args, ensure_ascii=False))
            yield StreamEvent("message_done", stop_reason="tool_use")
        else:
            yield StreamEvent("text_delta", text="摘要")
            yield StreamEvent("message_done", stop_reason="end")


def _candle(idx: int, price: float = _PX) -> Candle:
    return Candle(ts=_START_TS + idx * _STEP, o=price, h=price + 5, l=price - 5,
                  c=price, vol=100.0, confirm=True)


def _sizing_settings(**over) -> Settings:
    """打开总闸①②的运行配置（回看根数调小以便单测几根 K 线内就能攒够样本）。"""
    base = {"ai_trade_sizing_enabled": True, "ai_trade_stop_atr_mult": 1.5,
            "ai_trade_realized_vol_window": 3, "ai_trade_correlation_min_samples": 3,
            "ai_trade_funding_enabled": False, "ai_trade_reflection_enabled": False}
    base.update(over)
    return Settings(**base)


def _make_session(*, settings=None, action="open_long", size=1.0, risk=None, sizing=None):
    settings = settings or Settings()

    async def place_order_fn(order):
        return {"ok": True, "ordId": "x", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        ex = session.executor
        if ex.projected == "flat":
            return []
        return [{"instId": "BTC-USDT-SWAP", "pos": ex.entry_qty_sz, "upl": 0.0}]

    async def fills_fn():
        return []

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0,
        risk=risk or RiskParams(stop_loss_pct=0.05, take_profit_pct=0.10), ct_val=0.01,
        lot_sz=0.01, min_sz=0.01, settings=settings, place_order_fn=place_order_fn,
        close_position_fn=close_position_fn, positions_fn=positions_fn, fills_fn=fills_fn)
    decider = AiTradeDecider(_StubLLM(action, size), "sys", context_refresh_rounds=50,
                             settings=settings, position_step_pct=0.25)
    spec = PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})
    # [P1-7]：规则由**持有 settings 的一侧**解析后传入（生产是 manager，本用例照做）；会话自己不读
    # settings ⇒ 直接构造会话的既有单测拿到的是一个未装配的 runtime，逐字节零回归。
    session = AiTradeSession("aitrade_sizing", 1, [spec], "BTC-USDT-SWAP", "1H", executor,
                             decider, priming=False, priming_bars=10, context_refresh_rounds=50,
                             sizing=sizing or build_sizing_runtime(settings, "BTC-USDT-SWAP", 3600))

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


def _drain(q: asyncio.Queue) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def _traces(events, phase: str) -> list[dict]:
    return [e["data"] for e in events
            if e["type"] == "agent_trace" and e["data"]["phase"] == phase]


def _pin_atr(monkeypatch, value: float | None) -> None:
    monkeypatch.setattr("app.ai_auto.session.safe_atr", lambda *_a, **_k: value)


# ---------- T-4：全部开关关闭 ⇒ 逐字节回退 ----------
async def test_all_switches_off_is_byte_identical(monkeypatch):
    await init_db()
    session = _make_session()
    risk_before = session.executor.risk
    _pin_atr(monkeypatch, 300.0)
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        events = _drain(q)
        assert _traces(events, "sizing") == []            # trace 零事件
        assert session.executor.risk is risk_before       # `executor.risk` **对象** is 相等
        payload = session._build_decision_payload(1, _START_TS, _PX, [], "candle")
        assert "sizingPlan" not in payload                # payload 整键缺席 ⇒ prompt 逐字节回退
        assert session._sizing.armed is False
    finally:
        await dispose_engine()


# ---------- 装配后：ATR 止损真的写进 executor.risk，且 trace 每轮一条 ----------
async def test_atr_stop_override_lands_and_emits_trace(monkeypatch):
    await init_db()
    session = _make_session(settings=_sizing_settings())
    _pin_atr(monkeypatch, 300.0)
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        rows = _traces(_drain(q), "sizing")
        assert len(rows) == 1
        assert rows[0]["stopSource"] == "atr"
        # 1.5 × 300 / 30000 = 1.5%，止盈 = 2.0 × 1.5% = 3%
        assert abs(session.executor.risk.stop_loss_pct - 0.015) < 1e-9
        assert abs(session.executor.risk.take_profit_pct - 0.03) < 1e-9
        assert session.executor.risk.max_hold_bars is None      # max_hold_bars 原样保留
    finally:
        await dispose_engine()


# ---------- T-17b / T-17c（S-11）：两条状态前置条件不满足即一行不动 ----------
def test_risk_override_skipped_while_resting():
    risk = RiskParams(stop_loss_pct=0.05, take_profit_pct=0.10)
    executor = _FakeExecutor(risk=risk, resting=object())
    plan = StopPlan(stop_pct=0.02, take_pct=0.04, stop_bps=200.0, take_bps=400.0, source="atr")
    assert can_override_risk(executor) == "skipped_resting"
    assert apply_risk_override(executor, plan) == "skipped_resting"
    assert executor.risk is risk                       # 对象 is 相等 = 一行都没动


def test_risk_override_skipped_while_position_live():
    risk = RiskParams(stop_loss_pct=0.05, take_profit_pct=0.10)
    executor = _FakeExecutor(risk=risk, projected="long")
    plan = StopPlan(stop_pct=0.02, take_pct=0.04, stop_bps=200.0, take_bps=400.0, source="atr")
    assert apply_risk_override(executor, plan, "hold") == "skipped_position_live"
    assert executor.risk is risk
    # 反手（持有多头却要开空）单列一态，供 L1.5 度量 ATR 止损覆盖不到多少开仓。
    assert apply_risk_override(executor, plan, "open_short") == "skipped_reversal"
    assert executor.risk is risk


def test_risk_override_noop_when_plan_missing():
    risk = RiskParams(stop_loss_pct=0.05, take_profit_pct=0.10)
    executor = _FakeExecutor(risk=risk)
    assert apply_risk_override(executor, None) == "fixed"
    assert executor.risk is risk


class _FakeExecutor:
    """`can_override_risk` / `apply_risk_override` 只读三个字段，无需真 LiveExecutor。"""

    def __init__(self, *, risk, projected="flat", resting=None):
        self.risk = risk
        self.projected = projected
        self.resting = resting


# ---------- T-9：平仓 / 撤单在会话链路上永不被刹车拦下 ----------
async def test_close_is_never_blocked_by_brake(monkeypatch):
    await init_db()
    settings = _sizing_settings(ai_trade_brake_halt_dd_pct=0.01)
    session = _make_session(settings=settings, action="close")
    _pin_atr(monkeypatch, 300.0)
    try:
        session._sizing.note_equity(1000.0)
        session._sizing.note_equity(500.0)             # dd=50% ⇒ 第二档锁存
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        decision = [e["data"] for e in _drain(q) if e["type"] == "decision"][-1]
        assert decision["action"] == "close"           # 平仓一路直通
        assert decision["degradeReason"] is None
    finally:
        await dispose_engine()


async def test_halt_downgrades_open_to_hold(monkeypatch):
    await init_db()
    settings = _sizing_settings(ai_trade_brake_halt_dd_pct=0.01)
    session = _make_session(settings=settings, action="open_long")
    _pin_atr(monkeypatch, 300.0)
    try:
        session._sizing.note_equity(1000.0)
        session._sizing.note_equity(500.0)             # dd=50% ⇒ halt
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        decision = [e["data"] for e in _drain(q) if e["type"] == "decision"][-1]
        assert decision["action"] == "hold"
        assert decision["degradeReason"].startswith("drawdown_brake_halt(")
        assert session.executor.projected == "flat"
    finally:
        await dispose_engine()


# ---------- T-25（S-14）：敞口簿的名义额来自 `_dispatch` 唯一收口 ----------
async def test_notional_published_from_dispatch():
    await init_db()
    book = GroupExposureBook(unit_notional=10_000.0, cap_mult=1.5, bar_seconds=3600)
    settings = _sizing_settings()
    sizing = build_sizing_runtime(settings, "BTC-USDT-SWAP", 3600, book=book)
    session = _make_session(settings=settings, sizing=sizing)
    try:
        # 模拟限价挂单在**决策轮之外**被 `_adopt_fill` 采纳：executor 断面已是持仓，随后发一条 trade 事件。
        session.executor.projected = "long"
        session.executor.entry_qty_sz = 100.0
        session.executor.entry_price = _PX
        session._last_live_px = _PX
        await session._dispatch([{"type": "trade", "data": {"side": "long", "pnl": 0.0}}])
        view = book.effective_exposure(inst_id="OTHER", proposed_notional=1.0, min_samples=1)
        assert view is not None
        # 100 张 × ctVal 0.01 × 30000 = 30000 USDT，且**下一次读簿即可见**（不必等下一轮决策）。
        assert abs(view.effective - 30_001.0) < 1.0
    finally:
        await dispose_engine()


# ---------- T-29（[P0-2] 解耦）：k_min=0（默认）下凯利项仍可算 ----------
async def test_kelly_works_with_k_min_zero(monkeypatch):
    await init_db()
    settings = _sizing_settings()
    assert settings.ai_trade_edge_gate_k_min == 0.0     # 门禁空转是**默认值**
    session = _make_session(settings=settings)
    _pin_atr(monkeypatch, 300.0)
    try:
        cost_bps = session._cost.estimate_cost_only(
            TradeDecision(action="open_long", size_fraction=0.5, confidence=0.6, reason="r"), 3600)
        assert cost_bps is not None and cost_bps > 0    # 只读入口不受 k_min 门控
        # 有先验时凯利可算：直接喂一份先验视图，验证「总闸①不隐含依赖 k_min」。
        session._prior.coverage_wire = lambda: {"long": {"p": 0.6}, "short": None}
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        rows = _traces(_drain(q), "sizing")
        assert rows and rows[0]["kellyStatus"] == "ok"
        assert rows[0]["fKelly"] is not None
    finally:
        await dispose_engine()


def test_prior_p_win_reads_by_direction():
    view = {"long": {"p": 0.62}, "short": {"p": 0.41}}
    assert prior_p_win(view, "open_long") == 0.62
    assert prior_p_win(view, "open_short") == 0.41
    assert prior_p_win(view, "close") is None
    assert prior_p_win(None, "open_long") is None       # 先验层未装配 ⇒ 绝不凭空造 0.5
    assert prior_p_win({"long": None}, "open_long") is None


def test_disarmed_runtime_is_a_no_op():
    runtime = SizingRuntime()
    decision = TradeDecision(action="open_long", size_fraction=0.75, confidence=0.6, reason="r")
    out, degrade = runtime.gate(decision, None)
    assert out is decision and degrade is None
    resolved, plan, wire = runtime.resolve(decision, SizingContext())
    assert resolved is decision and plan is None and wire == {}
    assert runtime.plan_wire() is None


def test_resting_order_shape_is_what_can_override_reads():
    """`can_override_risk` 读的是 `executor.resting`；本用例钉住那个字段确实是挂单载体。"""
    assert "intent" in RestingOrder.__dataclass_fields__


# ---------- T-24 / G-8：两条对账真的挂在**启动期**（越界 422），而不是运行期静默 ----------
def _start_request(**over) -> AiTradeGroupStartRequest:
    base = {"specs": [PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})],
            "members_in": [AiTradeGroupMemberInput(inst_id="BTC-USDT-SWAP",
                                                   allocation_pct=100.0)],
            "bar": "1H", "account_row": None, "settings": Settings(),
            "context_refresh_rounds": 10, "priming_bars": 300}
    base.update(over)
    return AiTradeGroupStartRequest(**base)


def test_window_capacity_rejected_at_startup():
    # primingBars=10（schema 下界）⇒ 窗口上限只有 80 < 96+1，两个旗舰机制会永久静默失效 ⇒ 启动期拦下。
    req = _start_request(settings=_sizing_settings(ai_trade_realized_vol_window=96),
                         priming_bars=10)
    try:
        AiTradeManager._validate_sizing(req)
        raise AssertionError("窗口装不下回看根数却放行了")
    except AiTradePreflightError as exc:
        assert exc.code == "AI_TRADE_SIZING_WINDOW_TOO_SMALL" and exc.status == 422
    # 窗口够大即放行（默认 primingBars=300）。
    AiTradeManager._validate_sizing(_start_request(settings=_sizing_settings()))


def test_exposure_cap_out_of_range_rejected_at_startup():
    req = _start_request(settings=_sizing_settings(ai_trade_effective_exposure_cap_mult=0.5))
    try:
        AiTradeManager._validate_sizing(req)
        raise AssertionError("敞口上限 0<mult<1 会让单成员分组也被约束，却放行了")
    except AiTradePreflightError as exc:
        assert exc.code == "AI_TRADE_EXPOSURE_CAP_INVALID" and exc.status == 422


def test_sizing_validation_leaves_empty_group_to_its_own_error_code():
    """空分组归 `_validate_members` 的 `AI_TRADE_GROUP_EMPTY`（400）——本对账绝不先崩在下标上。

    `_validate_sizing` 排在 `_validate_members` **之前**，故它必须自己处理「还没有成员」这一态；
    直接取 `members_in[0]` 会把一个既有的 400 契约换成 `IndexError`（500）。
    """
    AiTradeManager._validate_sizing(_start_request(settings=_sizing_settings(), members_in=[]))
    AiTradeManager._validate_sizing(_start_request(members_in=[]))      # 总闸关：从不拦
