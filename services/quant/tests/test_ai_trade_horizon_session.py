"""持仓周期铁律层的会话级集成测试（ai-trade-holding-horizon-modes §6.1 · session 组）。

脱网、桩回调、桩 LLM。重点把守四件在纯函数单测里看不见的事：
1. **插入位序**：`base_rule → horizon → cooldown`（monkeypatch 记录调用顺序，可证伪）；
2. **被否轮零反思**：被本层拦下的轮次此时已是 hold ⇒ `decider.reflect` 调用次数**为 0**；
3. **双快照契约**（[P1-7]）：prompt 段的 `asOfWall` 早于 trace / decision 事件那一份；
4. **展示落点成对出现**：payload 有键 ⇒ trace 事件与 decision 事件同轮下发同一份 wire（G-4）。
"""
from __future__ import annotations

import asyncio
import json

from app.agent.llm.base import StreamEvent
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.hold_horizon import HorizonRule
from app.ai_auto.session import AiTradeSession, _compute_ai_stats
from app.config import Settings
from app.db import repositories as repo
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.okx.accounts import AccountCredentials
from app.okx.types import Candle
from app.paper.strategy_factory import PaperStrategySpec
from app.strategies.base import BaseStrategy, StrategyMeta

_START_TS = 1_700_000_000
_STEP = 3600
_PX = 30_000.0
_RULE = HorizonRule(profile_id="intraday_swing", title="日内波段", target_label="4 – 12 小时",
                    min_hold_s=7200.0, exit_confirm_rounds=2, confirm_window_s=3600.0,
                    hysteresis_k=1.5, target_hold_min_s=14400.0, target_hold_max_s=43200.0,
                    expected_hold_bars=12)


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_horizon_session", name="Stub", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


class _StubLLM:
    def __init__(self, actions: list[str]):
        self._actions = list(actions)

    async def stream(self, *, system, messages, tools):
        if tools:
            action = self._actions.pop(0) if self._actions else "hold"
            args = {"action": action, "reason": "r", "confidence": 0.7}
            if action in ("open_long", "open_short"):
                args["sizeFraction"] = 0.5
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


def _make_session(actions: list[str], *, horizon_rule=_RULE, run_id=1):
    settings = Settings(ai_trade_funding_enabled=False)
    calls = {"reflect": 0}

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
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(stop_loss_pct=0.05), ct_val=0.01,
        lot_sz=0.01, min_sz=0.01, settings=settings, place_order_fn=place_order_fn,
        close_position_fn=close_position_fn, positions_fn=positions_fn, fills_fn=fills_fn)
    decider = AiTradeDecider(_StubLLM(actions), "sys", context_refresh_rounds=50,
                             settings=settings)

    async def counting_reflect(*args, **kwargs):
        calls["reflect"] += 1
        raise AssertionError("被门禁拦下的轮次不应触发反思")

    decider.reflect = counting_reflect
    spec = PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})
    session = AiTradeSession("aitrade_horizon", run_id, [spec], "BTC-USDT-SWAP", "1H",
                             executor, decider, priming=False, priming_bars=10,
                             context_refresh_rounds=50, horizon_rule=horizon_rule)

    async def no_htf(*_a, **_k):
        return []

    session._window._safe_get_candles = no_htf
    return session, calls


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


async def test_gate_order_is_base_rule_then_horizon_then_cooldown(monkeypatch):
    """插入位序（§2.5 硬约束 3）：基础策略之后、冷却之前——`degrade or reason` 的归因才正确。"""
    order: list[str] = []
    session, _ = _make_session(["hold"])
    original_base = session._apply_base_rule_gate
    original_horizon = session._apply_horizon_gate
    original_cooldown = session._apply_profit_cooldown_gate

    async def base(*args, **kwargs):
        order.append("base_rule")
        return await original_base(*args, **kwargs)

    async def horizon(*args, **kwargs):
        order.append("horizon")
        return await original_horizon(*args, **kwargs)

    def cooldown(*args, **kwargs):
        order.append("cooldown")
        return original_cooldown(*args, **kwargs)

    monkeypatch.setattr(session, "_apply_base_rule_gate", base)
    monkeypatch.setattr(session, "_apply_horizon_gate", horizon)
    monkeypatch.setattr(session, "_apply_profit_cooldown_gate", cooldown)
    await init_db()
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        assert order == ["base_rule", "horizon", "cooldown"]
    finally:
        await dispose_engine()


async def test_flat_round_emits_idle_trace_and_never_gates_open():
    """空仓轮：本层不约束开仓；轨迹段照常每轮一条（**含放行轮**——静默的保守会被当成故障）。"""
    await init_db()
    session, _ = _make_session(["open_long"])
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        events = _drain(q)
        horizon_traces = _traces(events, "horizon")
        assert len(horizon_traces) == 1
        assert horizon_traces[0]["status"] == "idle"
        assert session.executor.projected == "long"
    finally:
        await dispose_engine()


async def test_close_veto_degrades_to_hold_without_reflection():
    """被本层拦下的 `close` ⇒ 降级 hold + 零反思 LLM 调用（门禁本身就是省钱项）。"""
    await init_db()
    session, calls = _make_session(["open_long", "close"])
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        q = session.subscribe()
        await session.feed_candle(_candle(1))
        await _settle(session)
        events = _drain(q)
        decision = [e["data"] for e in events if e["type"] == "decision"][-1]
        assert decision["action"] == "hold"
        assert decision["degradeReason"].startswith("horizon_min_hold(")
        assert calls["reflect"] == 0
        assert session.executor.projected == "long"      # 仓位还在，没有被平掉
        trace = _traces(events, "horizon")[-1]
        assert trace["status"] == "veto"
        assert trace["reason"] == decision["degradeReason"]
        # G-4：decision 事件带的 wire 与判定同源（同一份 gate 快照，不是 payload 那一份）。
        assert decision["horizon"]["asOfWall"] == trace["horizon"]["asOfWall"]
    finally:
        await dispose_engine()


async def test_prompt_snapshot_precedes_gate_snapshot():
    """[P1-7] 双快照契约：prompt 段的 `asOfWall` **早于** trace 那一份（中间隔着一次 LLM 调用）。"""
    await init_db()
    session, _ = _make_session(["open_long", "close"])
    payloads: list[dict] = []
    original = session._build_decision_payload

    def capture(*args, **kwargs):
        payload = original(*args, **kwargs)
        payloads.append(payload)
        return payload

    session._build_decision_payload = capture
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        q = session.subscribe()
        await session.feed_candle(_candle(1))
        await _settle(session)
        trace = _traces(_drain(q), "horizon")[-1]
        prompt_wire = payloads[-1]["tradingHorizon"]
        assert prompt_wire["asOfWall"] <= trace["horizon"]["asOfWall"]
        # 保护快照上的 `horizonArmed` 标记：§2.9 a/b 的两处抢跑文案据它切换。
        if payloads[-1].get("profitGuard"):
            assert payloads[-1]["profitGuard"]["horizonArmed"] is True
    finally:
        await dispose_engine()


async def test_unarmed_session_has_no_horizon_key_anywhere():
    """G-1 零回归：未装配 ⇒ payload 无 `tradingHorizon` 键、零 horizon 轨迹、status 该键为 None。"""
    await init_db()
    session, _ = _make_session(["open_long"], horizon_rule=None)
    payloads: list[dict] = []
    original = session._build_decision_payload

    def capture(*args, **kwargs):
        payload = original(*args, **kwargs)
        payloads.append(payload)
        return payload

    session._build_decision_payload = capture
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        assert "tradingHorizon" not in payloads[-1]
        assert _traces(_drain(q), "horizon") == []
        assert session._status_payload()["horizon"] is None
    finally:
        await dispose_engine()


async def test_finalize_merges_horizon_stats_at_the_call_site():
    """统计合并落在**调用点**：`_compute_ai_stats` 本身不含新键（可证伪「改了函数内部」）。"""
    await init_db()
    session, _ = _make_session(["open_long"], run_id=None)
    sm = get_sessionmaker()
    async with sm() as db:
        run = await repo.create_run(db, strategy_id=1, inst_id="BTC-USDT-SWAP", bar="1H",
                                    mode="ai-trade", config={"mode": "ai-trade"},
                                    status="running")
        session.run_id = run.id
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        await session.stop("user_stop")
        async with sm() as db:
            row = await repo.get_run(db, session.run_id)
        metrics = json.loads(row.metrics_json or "{}")
        horizon = metrics["aiStats"]["horizon"]
        assert horizon["profileId"] == "intraday_swing"
        assert horizon["roundTrips"] >= 1                 # stop 的 flatten 收了一条样本
        assert "horizon" not in _compute_ai_stats([], 0)  # 纯函数逐字未动
    finally:
        await dispose_engine()
