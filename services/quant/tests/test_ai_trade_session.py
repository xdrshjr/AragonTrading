"""AiTradeSession 单测（ai-auto-trading §2.6 / P1-1 / P1-2）：priming 不下真单、首实时确认根触发
一轮决策 → executor.apply、reconcile 校正 projected、会话级止损自动停、每 N 轮推 summary、
`_deciding` 跳过重叠轮。行情 / OKX / LLM 全程桩驱动（直接注入 feed_candle），铁律：占位假凭证。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.agent.llm.base import StreamEvent
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.session import AiTradeSession
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


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_ai_sess", name="Stub", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


class _StubLLM:
    """决策流（tools 非空）返回 submit_trade_decision；摘要流（tools 空）返回文本。"""

    def __init__(self, action="hold", size=0.5, summary="综合摘要：偏多"):
        self.action = action
        self.size = size
        self.summary = summary

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
            yield StreamEvent("text_delta", text=self.summary)
            yield StreamEvent("message_done", stop_reason="end")


def _candle(idx: int, price: float = 30000.0) -> Candle:
    return Candle(ts=_START_TS + idx * _STEP, o=price, h=price + 5, l=price - 5,
                  c=price, vol=100.0, confirm=True)


def _make_session(*, action="hold", size=0.5, run_id=1, stop_loss_usd=None,
                  pos_state=None, context_rounds=10, priming=False, settings=None):
    settings = settings or Settings()
    pos_state = pos_state if pos_state is not None else {"flat": False, "upl": 0.0}
    calls = {"orders": [], "closes": []}

    async def place_order_fn(order):
        calls["orders"].append(order)
        return {"ok": True, "ordId": "x", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        calls["closes"].append(close)
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        ex = session.executor
        if pos_state.get("flat") or ex.projected == "flat":
            return []
        return [{"instId": "BTC-USDT-SWAP", "pos": ex.entry_qty_sz,
                 "upl": pos_state.get("upl", 0.0)}]

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(), ct_val=0.01,
        lot_sz=0.01, min_sz=0.01, settings=settings,
        place_order_fn=place_order_fn, close_position_fn=close_position_fn,
        positions_fn=positions_fn)
    decider = AiTradeDecider(_StubLLM(action, size), "sys",
                             context_refresh_rounds=context_rounds, settings=settings)
    spec = PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})
    session = AiTradeSession("aitrade_test", run_id, [spec], "BTC-USDT-SWAP", "1H", executor,
                             decider, stop_loss_usd=stop_loss_usd, priming=priming,
                             priming_bars=10, context_refresh_rounds=context_rounds)

    async def no_htf_data(*_args, **_kwargs):
        return []

    session._window._safe_get_candles = no_htf_data
    return session, calls, pos_state


def test_disabled_multi_timeframe_context_does_not_allocate_policy_windows():
    session, _, _ = _make_session(
        settings=Settings(ai_trade_multi_timeframe_context=False),
    )

    assert session._window._context_bars == ()
    assert session._window._htf_bars == []


async def _settle(session) -> None:
    """等待本轮后台决策任务完成（决策以 asyncio.create_task 运行）。"""
    task = session._decision_task
    if task is not None:
        try:
            await task
        except asyncio.CancelledError:
            pass


def _drain(q: asyncio.Queue) -> list[dict]:
    out: list[dict] = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


class _FakeRest:
    def __init__(self, candles):
        self._c = candles

    async def get_candles(self, *_a, **_k):
        return self._c

    async def get_ticker(self, *_a, **_k):
        return 30000.0


async def test_priming_does_not_place_real_orders(monkeypatch):
    await init_db()
    history = [_candle(i) for i in range(10)]
    monkeypatch.setattr("app.ai_auto.market_window.get_rest_client",
                        lambda: _FakeRest(history))
    session, calls, _ = _make_session(action="open_long", priming=True)
    try:
        await session._prime()
        assert session._warmed_bars == 10
        assert calls["orders"] == [] and session.executor.trades == []
    finally:
        await dispose_engine()


async def test_first_candle_triggers_decision_and_opens():
    await init_db()
    session, calls, _ = _make_session(action="open_long")
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        assert session.executor.projected == "long"
        assert len(calls["orders"]) == 1
        assert session.rounds_completed == 1
    finally:
        await dispose_engine()


async def test_multi_timeframe_context_reaches_payload_trace_and_decision(monkeypatch):
    await init_db()
    session, _, _ = _make_session(action="hold")
    expected = {"executionBar": "1H", "asOfTs": _START_TS + _STEP,
                "frames": [], "missingBars": ["1D", "12H", "4H"]}
    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return expected

    monkeypatch.setattr("app.ai_auto.session.build_multi_timeframe_context", fake_build)
    try:
        queue = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        events = _drain(queue)
        round_start = next(
            event["data"] for event in events
            if event["type"] == "agent_trace" and event["data"]["phase"] == "round_start"
        )
        decision = next(event["data"] for event in events if event["type"] == "decision")

        assert captured["as_of_ts"] == _START_TS + _STEP
        assert captured["execution_bar"] == "1H"
        assert round_start["multiTimeframeContext"] is expected
        assert decision["multiTimeframeContext"] is expected
        assert session._round_trace["multiTimeframeContext"] is expected
    finally:
        await dispose_engine()


async def test_ai_size_fraction_reaches_trade_position_and_snapshot():
    await init_db()
    session, _, _ = _make_session(action="open_long", size=0.4)
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)

        events = _drain(q)
        trade = next(event["data"] for event in events if event["type"] == "trade")
        position = next(event["data"] for event in events if event["type"] == "position")
        snapshot = session.snapshot()

        assert trade["positionPct"] == pytest.approx(position["positionPct"])
        assert snapshot["trades"][-1]["positionPct"] == pytest.approx(
            trade["positionPct"]
        )
        assert snapshot["position"]["positionPct"] == pytest.approx(
            trade["positionPct"]
        )
    finally:
        await dispose_engine()


async def test_hold_decision_places_no_order():
    await init_db()
    session, calls, _ = _make_session(action="hold")
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        assert session.executor.projected == "flat"
        assert calls["orders"] == []
    finally:
        await dispose_engine()


async def test_reconcile_corrects_projected_to_flat():
    await init_db()
    session, calls, pos_state = _make_session(action="open_long")
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        assert session.executor.projected == "long"
        pos_state["flat"] = True   # 交易所侧已平（OCO / 外部）
        await session.feed_candle(_candle(1))
        await _settle(session)
        assert session.executor.projected == "flat"
    finally:
        await dispose_engine()


async def test_session_stop_loss_triggers_auto_stop():
    await init_db()
    sm = get_sessionmaker()
    async with sm() as s:
        run = await repo.create_run(s, strategy_id=1, inst_id="BTC-USDT-SWAP", bar="1H",
                                    mode="ai-trade", config={"mode": "ai-trade"})
        run_id = run.id
    session, calls, pos_state = _make_session(action="open_long", run_id=run_id,
                                              stop_loss_usd=50.0)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        assert session.executor.projected == "long"
        pos_state["upl"] = -100.0   # 浮亏 100 > 止损 50
        await session._monitor_tick()
        assert session.state == "stopped"
        assert session.stop_reason == "session_stop_loss"
        assert len(calls["closes"]) >= 1
        async with sm() as s:
            run = await repo.get_run(s, run_id)
            assert run.status == "stopped"
    finally:
        await dispose_engine()


async def test_decision_in_flight_skips_overlapping_round():
    await init_db()
    session, calls, _ = _make_session(action="open_long")
    try:
        q = session.subscribe()
        session._deciding = True                 # 模拟上一轮仍在进行
        await session.feed_candle(_candle(0))
        skipped = [e for e in _drain(q)
                   if e["type"] == "decision" and e["data"]["executed"].get("skipped")]
        assert skipped and skipped[0]["data"]["executed"]["reason"] == "decision_in_flight"
        assert calls["orders"] == []             # 跳过轮不下单
        assert session._decision_task is None     # 未启动新决策任务
    finally:
        await dispose_engine()


async def test_decision_event_carries_context_fields():
    """E2/E3：decision 事件含 marketContext/consensus/degradeReason 三键（向后兼容增补）。"""
    await init_db()
    session, _, _ = _make_session(action="hold")
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        data = [e for e in _drain(q) if e["type"] == "decision"][-1]["data"]
        assert "marketContext" in data          # 窗口 <60 根 → None，但键存在
        assert "consensus" in data and data["consensus"]["total"] == 1
        assert "degradeReason" in data and data["degradeReason"] is None  # 正常 hold 非降级
    finally:
        await dispose_engine()


async def test_consensus_gate_downgrades_open_to_hold_and_records_hold():
    """E2/E3 + R-P1-3：低共识开仓被硬降级 hold，executor.apply 未调用，且 journal 记录降级后的 hold。"""
    await init_db()
    # 桩策略从不交易 → 影子 stance 恒 flat → long_votes=0；min_consensus=1 使 open_long 必被拦。
    session, calls, _ = _make_session(action="open_long",
                                      settings=Settings(ai_trade_min_consensus=1))
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        assert calls["orders"] == []                    # executor.apply 未下单（门禁在其之前拦下）
        assert session.executor.projected == "flat"
        data = [e for e in _drain(q) if e["type"] == "decision"][-1]["data"]
        assert data["action"] == "hold"
        assert data["degradeReason"].startswith("low_consensus")
        assert "0/1" in data["degradeReason"]
        # R-P1-3：commit_round/journal 记录的是降级后的 hold（而非从未开过的仓），保持上下文纯净。
        assert session.decider._journal[-1]["decision"]["action"] == "hold"
    finally:
        await dispose_engine()


async def test_summary_emitted_every_n_rounds():
    await init_db()
    session, _, _ = _make_session(action="hold", context_rounds=2)
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        await session.feed_candle(_candle(1))
        await _settle(session)
        summaries = [e for e in _drain(q) if e["type"] == "summary"]
        assert summaries and summaries[0]["data"]["summary"] == "综合摘要：偏多"
        assert session.decider.summary == "综合摘要：偏多"
    finally:
        await dispose_engine()
