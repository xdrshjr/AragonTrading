"""AiTradeSession 单测（ai-auto-trading §2.6 / P1-1 / P1-2）：priming 不下真单、首实时确认根触发
一轮决策 → executor.apply、reconcile 校正 projected、会话级止损自动停、每 N 轮推 summary、
`_deciding` 跳过重叠轮。行情 / OKX / LLM 全程桩驱动（直接注入 feed_candle），铁律：占位假凭证。
"""
from __future__ import annotations

import asyncio
import json
import math
import time

import pytest

from app.agent.llm.base import StreamEvent
from app.ai_auto.base_rules import MaBandRule
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.profit_guard import ProfitGuardRule
from app.ai_auto.profit_guard_text import format_profit_guard_for_prompt
from app.ai_auto.prompts import REFLECTION_TOOL_NAME
from app.ai_auto.session import AiTradeSession
from app.config import Settings
from app.db import repositories as repo
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.okx.accounts import AccountCredentials
from app.okx.rest_client import OKXError
from app.okx.types import Candle
from app.paper.strategy_factory import PaperStrategySpec
from app.strategies.base import BaseStrategy, StrategyMeta

# wake-aware-context §2.7：`_build_decision_payload` 的第 5 形参由 `trigger: str` 换成结构化
# `wake: dict`（`wake["trigger"]` 承载原值，wire 逐字不变）。本常量只是把既有调用点的 "candle"
# 平移过来，**断言一字未改**。
_CANDLE_WAKE = {"trigger": "candle", "kind": "candle_close"}

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
                  pos_state=None, context_rounds=10, priming=False, settings=None,
                  base_rule=None, profit_rule=None):
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
                             priming_bars=10, context_refresh_rounds=context_rounds,
                             base_rule=base_rule, profit_rule=profit_rule)

    async def no_htf_data(*_args, **_kwargs):
        return []

    session._window._safe_get_candles = no_htf_data
    # 脱网守门（cost-truth-and-net-pnl I-1）：轴 A / 轴 C 的两条签名私有 GET 由 `CostRuntime` 在
    # 盯市 tick 的后台任务里发起。直接构造会话的用例带的是**假凭证**，不桩掉就会真的打 OKX——
    # 本文件承诺的是脱网单测，且真实往返会让每个 tick 多花数秒。
    session._cost._fetch_fee_rate = _no_trade_fee
    session._cost._fetch_position_history = _no_position_history
    session._cost.refresh_funding = _no_funding_refresh
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


# ---------- 下单前二次反思校验（ai-trade-pre-trade-reflection §6.2）----------
class _ReflectStubLLM:
    """决策流返回 submit_trade_decision；反思流返回 submit_trade_reflection；摘要流返回文本。

    以 tools[0].name 区分决策 / 反思两种工具流；记录 reflection_calls 供「hold 轮不反思」断言。
    """

    def __init__(self, *, action="open_long", size=0.5, verdict="confirm",
                 adjusted=None, summary="综合摘要：偏多"):
        self.action = action
        self.size = size
        self.verdict = verdict
        self.adjusted = adjusted
        self.summary = summary
        self.reflection_calls = 0

    async def stream(self, *, system, messages, tools):
        if not tools:                                  # 摘要流
            yield StreamEvent("text_delta", text=self.summary)
            yield StreamEvent("message_done", stop_reason="end")
            return
        if tools[0].name == REFLECTION_TOOL_NAME:      # 反思流
            self.reflection_calls += 1
            args = {"verdict": self.verdict, "reason": "复核结论"}
            if self.adjusted is not None:
                args["adjustedSizeFraction"] = self.adjusted
            yield StreamEvent("tool_use_start", id="rf", name=REFLECTION_TOOL_NAME)
            yield StreamEvent("tool_use_args_delta", id="rf",
                              partial_json=json.dumps(args, ensure_ascii=False))
            yield StreamEvent("message_done", stop_reason="tool_use")
            return
        args = {"action": self.action, "reason": "r", "confidence": 0.7}   # 决策流
        if self.action in ("open_long", "open_short"):
            args["sizeFraction"] = self.size
        yield StreamEvent("tool_use_start", id="d", name="submit_trade_decision")
        yield StreamEvent("tool_use_args_delta", id="d",
                          partial_json=json.dumps(args, ensure_ascii=False))
        yield StreamEvent("message_done", stop_reason="tool_use")


def _make_reflect_session(*, action="open_long", size=0.5, verdict="confirm",
                          adjusted=None, run_id=1, allow_limit=False, base_rule=None):
    """构造一个**打开反思**的会话（decider reflection_enabled=True，桩 LLM 区分决策/反思流）。

    `allow_limit`（默认 False → 既有反思剧本零回归）：True 时决策器放行 `cancel_order`
    （F-7 复现测试需要，ai-trade-limit-orders-fix §2.8）。
    """
    settings = Settings()
    calls = {"orders": [], "closes": []}

    async def place_order_fn(order):
        calls["orders"].append(order)
        return {"ok": True, "ordId": "x", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        calls["closes"].append(close)
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        ex = session.executor
        if ex.projected == "flat":
            return []
        return [{"instId": "BTC-USDT-SWAP", "pos": ex.entry_qty_sz, "upl": 0.0}]

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(), ct_val=0.01,
        lot_sz=0.01, min_sz=0.01, settings=settings,
        place_order_fn=place_order_fn, close_position_fn=close_position_fn,
        positions_fn=positions_fn)
    llm = _ReflectStubLLM(action=action, size=size, verdict=verdict, adjusted=adjusted)
    decider = AiTradeDecider(llm, "sys", context_refresh_rounds=10, settings=settings,
                             reflection_enabled=True, allow_limit=allow_limit)
    spec = PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})
    session = AiTradeSession("aitrade_reflect", run_id, [spec], "BTC-USDT-SWAP", "1H", executor,
                             decider, priming=False, priming_bars=10, context_refresh_rounds=10,
                             base_rule=base_rule)

    async def no_htf_data(*_args, **_kwargs):
        return []

    session._window._safe_get_candles = no_htf_data
    # 脱网守门（cost-truth-and-net-pnl I-1）：轴 A / 轴 C 的两条签名私有 GET 由 `CostRuntime` 在
    # 盯市 tick 的后台任务里发起。直接构造会话的用例带的是**假凭证**，不桩掉就会真的打 OKX——
    # 本文件承诺的是脱网单测，且真实往返会让每个 tick 多花数秒。
    session._cost._fetch_fee_rate = _no_trade_fee
    session._cost._fetch_position_history = _no_position_history
    session._cost.refresh_funding = _no_funding_refresh
    return session, calls, llm


async def test_reflection_confirm_lets_open_land():
    """反思 confirm → 开仓仍落地；发生了一次反思子调用；decision 事件 reflectionVerdict=confirm。"""
    await init_db()
    session, calls, llm = _make_reflect_session(action="open_long", verdict="confirm")
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        assert session.executor.projected == "long"
        assert len(calls["orders"]) == 1
        assert llm.reflection_calls == 1
        data = [e for e in _drain(q) if e["type"] == "decision"][-1]["data"]
        assert data["reflectionVerdict"] == "confirm"
    finally:
        await dispose_engine()


async def test_reflection_abort_blocks_real_order():
    """反思 abort → executor 未被调用（无真实下单）+ 降级 hold + decision 事件 reflectionVerdict=abort。"""
    await init_db()
    session, calls, llm = _make_reflect_session(action="open_long", verdict="abort")
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        assert calls["orders"] == []                     # 真实资金零动作
        assert session.executor.projected == "flat"
        assert llm.reflection_calls == 1
        data = [e for e in _drain(q) if e["type"] == "decision"][-1]["data"]
        assert data["action"] == "hold"
        assert data["degradeReason"] == "reflection_abort"
        assert data["reflectionVerdict"] == "abort"
        # commit_round/journal 记录的是反思后的 hold（保持上下文纯净）。
        assert session.decider._journal[-1]["decision"]["action"] == "hold"
    finally:
        await dispose_engine()


async def test_reflection_skipped_for_hold_decision():
    """hold 决策无交易意愿 → reflect 从不被调用（reflection_calls==0），无反思轨迹。"""
    await init_db()
    session, calls, llm = _make_reflect_session(action="hold")
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        assert llm.reflection_calls == 0
        assert calls["orders"] == []
        traces = [e for e in _drain(q)
                  if e["type"] == "agent_trace"
                  and e["data"]["phase"] in ("reflection_start", "reflection_verdict")]
        assert traces == []                              # hold 轮无反思轨迹
    finally:
        await dispose_engine()


async def test_reflection_confirm_shrinks_position_size():
    """反思 confirm + 更小 adjustedSizeFraction → 开仓仓位被收缩（只减不增），轨迹落收缩后仓位。"""
    await init_db()
    session, _, llm = _make_reflect_session(action="open_long", size=0.8,
                                            verdict="confirm", adjusted=0.2)
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        verdict_evt = next(
            e["data"] for e in _drain(q)
            if e["type"] == "agent_trace" and e["data"]["phase"] == "reflection_verdict")
        assert verdict_evt["verdict"] == "confirm"
        assert abs(verdict_evt["adjustedSizeFraction"] - 0.2) < 1e-9
        assert abs(session._round_trace["reflectionAdjustedSize"] - 0.2) < 1e-9
    finally:
        await dispose_engine()


async def test_reflection_emits_start_and_verdict_trace_phases():
    """交易意愿轮反思发 reflection_start + reflection_verdict 两阶段轨迹（ReAct 复核留痕）。"""
    await init_db()
    session, _, _ = _make_reflect_session(action="open_long", verdict="confirm")
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        phases = [e["data"]["phase"] for e in _drain(q) if e["type"] == "agent_trace"]
        assert "reflection_start" in phases
        assert "reflection_verdict" in phases
    finally:
        await dispose_engine()


async def test_cancel_order_bypasses_reflection():
    """F-7 复现（ai-trade-limit-orders-fix §2.8，先红后绿）：撤单不进反思。

    `cancel_order` 不产生任何委托（order_plan 恒 None），无「下单前」可复核；反思复核官的
    词汇表只认识 open/close，abort 剧本（或生产网络抖动 fail-closed）会把撤单降级 hold——
    挂单敞口反而多活到 TTL。故撤单须与 hold 一样直通：零反思子调用、动作不被降级。
    """
    await init_db()
    session, calls, llm = _make_reflect_session(action="cancel_order", verdict="abort",
                                                allow_limit=True)
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        assert llm.reflection_calls == 0                 # 撤单绝不触发反思子调用
        data = [e for e in _drain(q) if e["type"] == "decision"][-1]["data"]
        assert data["action"] == "cancel_order"          # abort 剧本下也不被降级 hold
        assert calls["orders"] == [] and calls["closes"] == []   # 撤单不开不平
    finally:
        await dispose_engine()


# ---------- F2 否决意图记忆保真（ai-trade-reflection-hold-inertia-fix §六-2，先红后绿复现）----------
def _last_tool_result_text(session) -> str:
    """取 decider._context 末条 tool_result 文本（commit_round 写入的执行回执）。"""
    return session.decider._context[-1].content[0].content


async def test_journal_preserves_vetoed_intent():
    """反思否决轮：journal 带 reflection.vetoed=True + 原提议动作；_context tool_result 含注记；
    同时保持既有断言 journal.decision.action == "hold"（R-P1-3 不变式仍成立）。"""
    await init_db()
    session, calls, _ = _make_reflect_session(action="open_long", size=0.5, verdict="abort")
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        assert calls["orders"] == []                     # 否决轮真实资金零动作
        entry = session.decider._journal[-1]
        assert entry["decision"]["action"] == "hold"     # R-P1-3：journal 记最终 hold
        note = entry["reflection"]
        assert note["vetoed"] is True
        assert note["failClosed"] is False
        assert note["proposedAction"] == "open_long"
        assert abs(note["proposedSizeFraction"] - 0.5) < 1e-9
        assert note["finalAction"] == "hold"
        assert entry["trigger"] == "candle"
        assert '"reflection"' in _last_tool_result_text(session)   # 短窗口上下文可见否决注记
    finally:
        await dispose_engine()


def _assert_v2_round_context(session, entry, text):
    """程序事实按 v2 合同追加，原执行结果及真实上下文原文完整保留。"""
    keys = ("schemaVersion", "proposal", "finalDecision", "facts", "factsHash",
            "executionReceipt", "gateVeto", "sizingVerdict", "closedBarTs",
            "evidenceFingerprint", "costModelVersion")
    expected = {**entry["executed"], **{key: entry[key] for key in keys}}
    assert text == json.dumps(expected, ensure_ascii=False)
    assert expected["schemaVersion"] == 2
    assert expected["factsHash"] == expected["facts"]["factsHash"]
    assert expected["factsHash"] == session._round_facts["factsHash"]
    latest = session.decider._context[-1]
    assert latest.role == "tool"
    assert latest.content[0].content == text
    assert "reflection" not in json.loads(text)


async def test_non_vetoed_round_tool_result_preserves_execution_and_v2_facts():
    """confirm 及未触发反思轮保留执行原值，并携带同一轮事实而不伪造否决注记。"""
    await init_db()
    # confirm 直通轮（触发了反思但未否决）
    session, _, llm = _make_reflect_session(action="open_long", verdict="confirm")
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        assert llm.reflection_calls == 1
        entry = session.decider._journal[-1]
        assert entry["reflection"]["vetoed"] is False    # journal 保真注记
        text = _last_tool_result_text(session)
        _assert_v2_round_context(session, entry, text)
    finally:
        await dispose_engine()
    await init_db()
    # 未触发反思的 hold 轮（reflection 注记为 None）
    session, _, llm = _make_reflect_session(action="hold")
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        assert llm.reflection_calls == 0
        entry = session.decider._journal[-1]
        assert entry["reflection"] is None
        text = _last_tool_result_text(session)
        _assert_v2_round_context(session, entry, text)
    finally:
        await dispose_engine()


# ---------- F5 aiStats 反思四计数（hold-inertia-fix §2.5，R-04 aborts/failClosed 分流）----------
def test_compute_ai_stats_reflection_counters_split_fail_closed():
    """reflectionAborts 只计模型真实否决（degradeReason=="reflection_abort"）；四个基础设施码计入
    reflectionFailClosed——一次 529 类过载抖动不得抬高否决率（R-04 口径）。"""
    from app.ai_auto.session import _compute_ai_stats
    dec = [
        {"action": "open_long", "confidence": 0.8, "degradeReason": None,
         "reflectionVerdict": "confirm"},                                  # 真实确认
        {"action": "hold", "confidence": None, "degradeReason": "reflection_abort",
         "reflectionVerdict": "abort"},                                    # 模型真实否决
        {"action": "hold", "confidence": None, "degradeReason": "reflection_timeout",
         "reflectionVerdict": "abort"},                                    # fail-closed（非真实否决）
        {"action": "hold", "confidence": None, "degradeReason": "reflection_llm_error",
         "reflectionVerdict": "abort"},                                    # fail-closed
        {"action": "hold", "confidence": 0.6, "degradeReason": None,
         "reflectionVerdict": None},                                       # 未触发反思
    ]
    st = _compute_ai_stats(dec, 5)
    assert st["reflectionRuns"] == 4                     # reflectionVerdict 非 None 的样本数
    assert st["reflectionConfirms"] == 1
    assert st["reflectionAborts"] == 1                   # 只计真实否决，不含 fail-closed
    assert st["reflectionFailClosed"] == 2
    # 既有键零回归
    assert st["decisionRounds"] == 5
    assert st["degradedRounds"] == 3
    assert st["holdRate"] == pytest.approx(0.8)


def test_compute_ai_stats_reflection_counters_zero_on_legacy_records():
    """legacy 决策记录（无 reflectionVerdict 键）与空样本：四计数恒 0、绝不抛。"""
    from app.ai_auto.session import _compute_ai_stats
    legacy = [{"action": "hold", "confidence": None, "degradeReason": None}]
    st = _compute_ai_stats(legacy, 1)
    assert st["reflectionRuns"] == 0
    assert st["reflectionConfirms"] == 0
    assert st["reflectionAborts"] == 0
    assert st["reflectionFailClosed"] == 0
    empty = _compute_ai_stats([], 0)
    assert empty["reflectionRuns"] == 0 and empty["reflectionFailClosed"] == 0


# ---------- N2/N3/N5 连续观望自察（ai-trade-hold-inertia-observability §六-1/2/4）----------
def test_hold_streak_rules():
    """N2 表驱动：非 hold 归零 / 真实 hold +1（max 跟随）/ 降级轮不变——含 low_confidence 分支（R2-03，
    decide() 置信度门禁改写轮按弱意愿中性处理，不归零）；被门禁/反思否决的轮以第一阶段意愿为准。"""
    session, _, _ = _make_session(action="hold")
    cases = [
        # (stage1_action, stage1_degrade, expected_streak)
        ("hold", None, 1),                        # 真实观望 +1
        ("hold", None, 2),                        # 连续累积
        ("hold", "llm_error", 2),                 # 安全回退降级轮：不变
        ("hold", "low_confidence", 2),            # 置信度门禁改写轮：不变（R2-03 语义钉）
        ("hold", None, 3),                        # 恢复累积，max 跟随至 3
        ("open_long", None, 0),                   # 交易意愿 → 归零
        ("hold", None, 1),
        ("open_short", "reflection_abort", 0),    # 否决轮以第一阶段意愿为准 → 仍归零
    ]
    for action, degrade, expected in cases:
        session._update_hold_streak(action, degrade)
        assert session.hold_streak == expected, (action, degrade)
    assert session.max_hold_streak == 3           # 会话级全程最大值


def test_payload_hold_streak_threshold():
    """N3：显式 Settings(ai_trade_hold_streak_note_after=2)（防默认值漂移，沿 R-03 显式构造范式）——
    达阈注入 holdStreak；未达无键；=0 关闭时 hold_streak=99 也无键（prompt 逐字节回退口）。"""
    session, _, _ = _make_session(settings=Settings(ai_trade_hold_streak_note_after=2))
    session.hold_streak = 2
    payload = session._build_decision_payload(1, _START_TS, 30000.0, [], _CANDLE_WAKE)
    assert payload["holdStreak"] == 2
    session.hold_streak = 1
    payload = session._build_decision_payload(2, _START_TS, 30000.0, [], _CANDLE_WAKE)
    assert "holdStreak" not in payload
    session_off, _, _ = _make_session(settings=Settings(ai_trade_hold_streak_note_after=0))
    session_off.hold_streak = 99
    payload = session_off._build_decision_payload(3, _START_TS, 30000.0, [], _CANDLE_WAKE)
    assert "holdStreak" not in payload


async def test_decision_event_and_status_carry_hold_streak():
    """N5：桩 LLM 连续 hold → decision 事件 data["holdStreak"] 随轮递增、_status_payload 同步；
    _finalize_run 后 metrics.aiStats.maxHoldStreak == 历史最大值（会话级直取，非样本口径）。"""
    await init_db()
    sm = get_sessionmaker()
    async with sm() as s:
        run = await repo.create_run(s, strategy_id=1, inst_id="BTC-USDT-SWAP", bar="1H",
                                    mode="ai-trade", config={"mode": "ai-trade"})
        run_id = run.id
    session, _, _ = _make_session(action="hold", run_id=run_id)
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        await session.feed_candle(_candle(1))
        await _settle(session)
        decisions = [e["data"] for e in _drain(q) if e["type"] == "decision"]
        assert [d["holdStreak"] for d in decisions] == [1, 2]
        assert session._status_payload()["holdStreak"] == 2
        await session.stop("user_stop")
        async with sm() as s:
            row = await repo.get_run(s, run_id)
        stats = json.loads(row.metrics_json)["aiStats"]
        assert stats["maxHoldStreak"] == 2
    finally:
        await dispose_engine()


async def test_finalize_run_persists_reflection_counters_and_wake_skips():
    """会话停止后 metrics.aiStats 含反思四键与 wakeSkips（R-07：事后可验证 F4c 生效）。"""
    await init_db()
    sm = get_sessionmaker()
    async with sm() as s:
        run = await repo.create_run(s, strategy_id=1, inst_id="BTC-USDT-SWAP", bar="1H",
                                    mode="ai-trade", config={"mode": "ai-trade"})
        run_id = run.id
    session, _, _ = _make_reflect_session(action="open_long", verdict="abort", run_id=run_id)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        session.wake_skips = 7                           # 模拟守卫抑制过 7 次唤醒
        await session.stop("user_stop")
        async with sm() as s:
            row = await repo.get_run(s, run_id)
        stats = json.loads(row.metrics_json)["aiStats"]
        assert stats["reflectionRuns"] == 1
        assert stats["reflectionAborts"] == 1
        assert stats["reflectionConfirms"] == 0
        assert stats["reflectionFailClosed"] == 0
        assert stats["wakeSkips"] == 7
    finally:
        await dispose_engine()


# ---------- 【基础策略】铁律门禁（ai-trade-base-strategy-ma-band §6.1 用例 13~16）----------
_BASE_RULE = MaBandRule()
_SEED_BARS = 24                   # > period+1，保证通道可算


def _seed_window(session, closes: list[float]) -> None:
    """直接种入行情窗口（**不触发决策轮**），使随后 feed 的那一根落在可控价位。"""
    for i, close in enumerate(closes):
        session._window.prime_append({"ts": _START_TS + i * _STEP, "o": close,
                                      "h": close + 1, "l": close - 1,
                                      "c": close, "vol": 100.0})


def _wavy_closes(n: int = _SEED_BARS) -> list[float]:
    return [30000.0 + 120.0 * math.sin(i / 2.0) for i in range(n)]


def _mid_price(closes: list[float], period: int = 20) -> float:
    """使 `feed` 后的收盘价**恰为中轨**（%B=0.5）的解析解：p = mean(最后 period-1 根)。

    中轨 = SMA(period) 含被喂入的这根本身，故 p = (Σ前 period-1 根 + p)/period ⇒ p = Σ/(period-1)。
    """
    tail = closes[-(period - 1):]
    return sum(tail) / len(tail)


async def test_base_rule_gate_runs_before_consensus_and_reflection():
    """带外开仓被铁律否决 ⇒ 最终 hold + base_rule 降级码 + **反思调用次数 == 0**（P1-2 顺序冻结）。"""
    await init_db()
    session, calls, llm = _make_reflect_session(action="open_long", base_rule=_BASE_RULE)
    try:
        closes = _wavy_closes()
        _seed_window(session, closes)
        q = session.subscribe()
        far = _mid_price(closes) * 1.05          # 高出中轨 5%（带宽仅约 1%）⇒ 必在带外
        await session.feed_candle(_candle(_SEED_BARS, price=far))
        await _settle(session)
        data = next(e["data"] for e in _drain(q) if e["type"] == "decision")
        assert data["action"] == "hold"
        assert data["degradeReason"].startswith("base_rule_ma_band(pctB=")
        assert llm.reflection_calls == 0          # 被否轮绝不烧一次反思 LLM 调用
        assert calls["orders"] == []              # 反手两腿都不发生
    finally:
        await dispose_engine()


async def test_base_rule_gate_lets_anchor_zone_entry_through():
    """参考价恰在中轨锚区 ⇒ 铁律放行、开仓照常落地、轨迹状态为 pass。"""
    await init_db()
    session, calls, _ = _make_session(action="open_long", base_rule=_BASE_RULE)
    try:
        closes = _wavy_closes()
        _seed_window(session, closes)
        q = session.subscribe()
        await session.feed_candle(_candle(_SEED_BARS, price=_mid_price(closes)))
        await _settle(session)
        events = _drain(q)
        data = next(e["data"] for e in events if e["type"] == "decision")
        assert data["action"] == "open_long" and data["degradeReason"] is None
        gate = next(e["data"] for e in events
                    if e["type"] == "agent_trace" and e["data"]["phase"] == "base_rule")
        assert gate["status"] == "pass" and gate["zone"] == "mid"
        assert len(calls["orders"]) == 1
    finally:
        await dispose_engine()


async def test_hold_streak_counts_model_intent_not_gate():
    """被铁律否决的轮次不增加 hold_streak（N2：惯性以模型第一阶段真实意愿为准）。"""
    await init_db()
    session, _, _ = _make_session(action="open_long", base_rule=_BASE_RULE)
    try:
        closes = _wavy_closes()
        _seed_window(session, closes)
        await session.feed_candle(_candle(_SEED_BARS, price=_mid_price(closes) * 1.05))
        await _settle(session)
        assert session._last_decision["degradeReason"].startswith("base_rule_ma_band(")
        assert session.hold_streak == 0
    finally:
        await dispose_engine()


async def test_emits_base_rule_trace_each_round():
    """规则装配 ⇒ 每轮一条 `base_rule` 轨迹；未装配 ⇒ **零条**（老 UI 与既有断言零影响）。"""
    await init_db()
    session, _, _ = _make_session(action="hold", base_rule=_BASE_RULE)
    try:
        _seed_window(session, _wavy_closes())
        q = session.subscribe()
        await session.feed_candle(_candle(_SEED_BARS))
        await _settle(session)
        assert sum(1 for e in _drain(q)
                   if e["type"] == "agent_trace" and e["data"]["phase"] == "base_rule") == 1
        assert session._round_trace["baseRuleStatus"] == "pass"
    finally:
        await dispose_engine()

    await init_db()
    plain, _, _ = _make_session(action="hold")
    try:
        q = plain.subscribe()
        await plain.feed_candle(_candle(0))
        await _settle(plain)
        assert not [e for e in _drain(q)
                    if e["type"] == "agent_trace" and e["data"]["phase"] == "base_rule"]
        assert plain._round_trace["baseRuleStatus"] is None
        assert "baseRules" not in plain._build_decision_payload(1, 0, 1.0, [], _CANDLE_WAKE)
    finally:
        await dispose_engine()


async def test_three_consecutive_unavailable_rounds_emit_error_event():
    """通道连续 3 轮算不出 ⇒ fail-closed 否决开仓 + 第 3 轮追发 AI_TRADE_BASE_RULE_UNAVAILABLE（P0-4）。"""
    await init_db()
    session, calls, _ = _make_session(action="open_long", base_rule=_BASE_RULE)
    try:
        q = session.subscribe()
        for i in range(3):                        # 窗口远不足 period+1 ⇒ band 恒 None
            await session.feed_candle(_candle(i))
            await _settle(session)
        events = _drain(q)
        decisions = [e["data"] for e in events if e["type"] == "decision"]
        assert [d["degradeReason"] for d in decisions] == ["base_rule_band_unavailable"] * 3
        assert calls["orders"] == []
        errors = [e["data"] for e in events
                  if e["type"] == "error" and e["data"].get("code") == "AI_TRADE_BASE_RULE_UNAVAILABLE"]
        assert len(errors) == 1                   # 恰一条（阈值等号触发，不每轮刷屏）
        assert [e["data"]["status"] for e in events
                if e["type"] == "agent_trace"
                and e["data"]["phase"] == "base_rule"] == ["unavailable"] * 3
    finally:
        await dispose_engine()


# ============================ 利润保护层（ai-trade-profit-protection §7.1）============================
_PG_ENTRY = 0.07245
_PG_LEV = 5.0
_PG_QTY = 100.0


def _pg_rule(**kwargs) -> ProfitGuardRule:
    return ProfitGuardRule(**kwargs)


def _pg_peak_px(return_pct: float, *, entry: float = _PG_ENTRY) -> float:
    """由目标峰值保证金收益率反算峰值价（构造式基准，[P1-3]：不写死任何字面价）。"""
    return entry * (1 + return_pct / _PG_LEV)


def _pg_trail_px(peak_px: float, rule: ProfitGuardRule, *, entry: float = _PG_ENTRY) -> float:
    return entry + (peak_px - entry) * (1 - rule.giveback_pct)


def _open_long(session, *, entry: float = _PG_ENTRY, qty: float = _PG_QTY) -> None:
    """把自管持仓直接置为一笔多头（镜像 `_do_open` 市价分支的状态跃迁，不经 LLM / 下单桩）。"""
    ex = session.executor
    ex.leverage = _PG_LEV
    ex.projected = "long"
    ex.entry_price = entry
    ex.entry_qty_sz = qty
    ex.entry_bar = 0
    ex.entry_position_pct = 0.5
    session._last_live_px = entry
    session._last_px_wall = time.time()


def _arm_peak(session, peak_px: float, *, aged_s: float = 300.0) -> None:
    """把峰值推到 `peak_px` 并让最短持有期过去（`opened_wall` 回拨）。"""
    session._last_live_px = peak_px
    session._refresh_profit_state(time.time())
    session._guard.state.opened_wall -= aged_s


async def test_profit_guard_absent_rule_is_byte_identical():
    """G-4：`profit_rule=None` ⇒ payload 无 profitGuard 键、position 无保护段、盯市零事件。"""
    await init_db()
    session, _, _ = _make_session(action="hold")
    try:
        _open_long(session)
        q = session.subscribe()
        payload = session._build_decision_payload(1, 0, _PG_ENTRY, [], _CANDLE_WAKE)
        assert "profitGuard" not in payload and "profitHoldRounds" not in payload
        assert "profitGuard" not in (session._position_payload(_PG_ENTRY) or {})
        await session._maybe_profit_exit(_START_TS)
        assert _drain(q) == [] and session.profit_guard_exits == 0
    finally:
        await dispose_engine()


async def test_profit_guard_trailing_exit_closes_position_and_counts():
    """验收 #1：浮盈自峰值回吐超阈 ⇒ 在浮盈仍为正时产生 close 成交（reason/explain 为保护码）。"""
    await init_db()
    rule = _pg_rule()
    session, _, _ = _make_session(action="hold", profit_rule=rule)
    try:
        _open_long(session)
        q = session.subscribe()
        peak = _pg_peak_px(0.10)
        _arm_peak(session, peak)
        session._last_live_px = _pg_trail_px(peak, rule)

        await session._maybe_profit_exit(_START_TS)

        trades = [e["data"] for e in _drain(q) if e["type"] == "trade"]
        assert [t["side"] for t in trades] == ["close"]
        assert trades[0]["reason"] == "profit_guard_trailing"
        assert trades[0]["explain"]["kind"] == "profit_guard"
        assert trades[0]["pnl"] > 0                      # 锁定的是正浮盈，不是翻负后的止损
        assert session.executor.projected == "flat"
        assert session.profit_guard_exits == 1
        assert session._position_seq == 1                # P0-1：close 成交驱动持仓序号
    finally:
        await dispose_engine()


async def test_profit_guard_reopen_same_side_starts_new_epoch():
    """T-3 [P0-1]：保护平仓后同向立即重开的新仓，peak 从新入场价起算且 min_hold 内不被平掉。"""
    await init_db()
    rule = _pg_rule()
    session, _, _ = _make_session(action="hold", profit_rule=rule)
    try:
        _open_long(session)
        peak = _pg_peak_px(0.10)
        trail = _pg_trail_px(peak, rule)
        _arm_peak(session, peak)
        session._last_live_px = trail
        await session._maybe_profit_exit(_START_TS)
        assert session.executor.projected == "flat" and session._position_seq == 1

        _open_long(session, entry=trail)                 # 同一 monitor 间隔内同向重开
        snapshot = session._refresh_profit_state(time.time())
        assert snapshot["holdSeconds"] < 1.0             # 持仓时长从 0 起算
        assert snapshot["peakPx"] == pytest.approx(trail)   # 旧峰值未被继承
        assert snapshot["status"] == "idle"

        await session._maybe_profit_exit(_START_TS + 1)

        assert session.executor.projected == "long"      # min_hold 重新生效 ⇒ 新仓不被强平
        assert session.profit_guard_exits == 1
    finally:
        await dispose_engine()


async def test_profit_guard_failed_close_does_not_count_or_claim_success():
    """T-4 [P1-1]：平仓请求失败 ⇒ 不计数、不写 lastExit、文案不含「已市价平仓」，下轮自动重试。"""
    await init_db()
    rule = _pg_rule()
    session, _, _ = _make_session(action="hold", profit_rule=rule)
    try:
        attempts = {"n": 0}

        async def failing_close(_close):
            attempts["n"] += 1
            raise OKXError("LIVE_ORDER_FAILED", "交易所拒绝")

        session.executor._close_position = failing_close
        _open_long(session)
        q = session.subscribe()
        peak = _pg_peak_px(0.10)
        _arm_peak(session, peak)
        session._last_live_px = _pg_trail_px(peak, rule)

        await session._maybe_profit_exit(_START_TS)
        await session._maybe_profit_exit(_START_TS + 5)   # 下一次盯市自动重试

        events = _drain(q)
        assert attempts["n"] == 2                         # 失败即保留状态、下轮重试
        assert session.profit_guard_exits == 0
        assert session._guard.pending_protect_note is None
        assert session.executor.projected == "long"
        messages = [e["data"].get("message") or "" for e in events if e["type"] == "status"]
        assert not any("已市价平仓" in m for m in messages)
        warns = [m for m in messages if "平仓未成交" in m]
        assert len(warns) == 1                            # P2-8：每个持仓周期至多一条 warn
    finally:
        await dispose_engine()


async def test_profit_guard_stale_price_takes_no_action():
    """T-5 [P1-6]：判定价超龄 ⇒ 零事件、快照 unavailable(stale_feed)、prompt 不渲染任何退出线。"""
    await init_db()
    rule = _pg_rule()
    session, _, _ = _make_session(action="hold", profit_rule=rule)
    try:
        _open_long(session)
        peak = _pg_peak_px(0.10)
        _arm_peak(session, peak)
        session._last_live_px = _pg_trail_px(peak, rule)
        session._last_px_wall = time.time() - (rule.max_px_age_s + 30)
        q = session.subscribe()

        await session._maybe_profit_exit(_START_TS)

        assert _drain(q) == [] and session.executor.projected == "long"
        snapshot = session._refresh_profit_state(time.time())
        assert snapshot["status"] == "unavailable"
        assert snapshot["unavailableReason"] == "stale_feed"
        text = format_profit_guard_for_prompt(snapshot)
        assert "当前不可用" in text and "有效退出线" not in text
    finally:
        await dispose_engine()


async def test_profit_guard_last_exit_is_consumed_after_one_round():
    """T-6 [P1-7]：一次保护平仓只在紧接的下一轮 payload 出现一次 lastExit，再下一轮不再出现。"""
    await init_db()
    rule = _pg_rule()
    session, _, _ = _make_session(action="hold", profit_rule=rule)
    try:
        _open_long(session)
        peak = _pg_peak_px(0.10)
        _arm_peak(session, peak)
        session._last_live_px = _pg_trail_px(peak, rule)
        await session._maybe_profit_exit(_START_TS)
        assert session.profit_guard_exits == 1

        first = session._build_decision_payload(1, _START_TS, _PG_ENTRY, [], _CANDLE_WAKE)
        second = session._build_decision_payload(2, _START_TS, _PG_ENTRY, [], _CANDLE_WAKE)

        assert first["profitGuard"]["lastExit"]["reasonCode"] == "profit_guard_trailing"
        assert first["profitGuard"]["status"] == "flat"   # 已平仓：空壳快照只承载回执
        assert "profitGuard" not in second                # 回执读取即消费，不再重复注入
    finally:
        await dispose_engine()


async def test_profit_guard_snapshot_ignores_caller_mark_px():
    """G-6 [P1-2]：判定价单源——不同 mark_px 传参下保护段逐键相等且 livePx 恒为 `_current_px()`。"""
    await init_db()
    rule = _pg_rule()
    session, _, _ = _make_session(action="hold", profit_rule=rule)
    try:
        _open_long(session)
        live = _pg_peak_px(0.06)
        session._last_live_px = live

        first = session._position_payload(_PG_ENTRY * 0.90)["profitGuard"]
        second = session._position_payload(_PG_ENTRY * 1.30)["profitGuard"]

        assert first == second
        assert first["livePx"] == pytest.approx(session._current_px())
        assert first["livePx"] == pytest.approx(live)
        snapshot = session._refresh_profit_state(time.time())
        assert snapshot["effectiveExitPx"] == first["effectiveExitPx"]
    finally:
        await dispose_engine()


async def test_profit_guard_triggers_between_confirmed_candles():
    """验收 #2：保护判定不等 K 线确认——两根之间注入实时价即可在 `_monitor_tick` 内触发。"""
    await init_db()
    rule = _pg_rule()
    session, _, _ = _make_session(action="hold", profit_rule=rule)
    try:
        await session.feed_candle(_candle(0))            # 建立窗口（hold 轮，不开仓）
        await _settle(session)
        _open_long(session)
        peak = _pg_peak_px(0.10)
        _arm_peak(session, peak)
        q = session.subscribe()
        trail = _pg_trail_px(peak, rule)
        unconfirmed = Candle(ts=_START_TS + _STEP, o=peak, h=peak, l=trail,
                             c=trail, vol=1.0, confirm=False)

        await session.feed_candle(unconfirmed)           # 未确认根：只更新实时价
        await session._monitor_tick()

        trades = [e["data"] for e in _drain(q) if e["type"] == "trade"]
        assert [t["reason"] for t in trades] == ["profit_guard_trailing"]
        assert session.executor.projected == "flat"
    finally:
        await dispose_engine()


async def test_profit_guard_metrics_land_in_ai_stats():
    """验收 #9：`profitGuardExits` = 实际成交次数；`profitGuardArmedRounds` = 轮级武装计数。"""
    await init_db()
    rule = _pg_rule()
    session, _, _ = _make_session(action="hold", profit_rule=rule)
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            run = await repo.create_run(s, strategy_id=1, inst_id="BTC-USDT-SWAP", bar="1H",
                                        mode="ai-trade", config={}, status="running")
            session.run_id = run.id
        _open_long(session)
        peak = _pg_peak_px(0.10)
        _arm_peak(session, peak)
        session._build_decision_payload(1, _START_TS, _PG_ENTRY, [], _CANDLE_WAKE)
        session._last_live_px = _pg_trail_px(peak, rule)
        await session._maybe_profit_exit(_START_TS)

        await session._finalize_run(_START_TS)

        async with sm() as s:
            row = await repo.get_run(s, session.run_id)
        stats = json.loads(row.metrics_json)["aiStats"]
        assert stats["profitGuardExits"] == 1
        assert stats["profitGuardArmedRounds"] == 1
    finally:
        await dispose_engine()


async def test_profit_hold_streak_only_counts_undegraded_profitable_holds():
    """§2.6(c) 逐分支：hold+浮盈 → +1；hold+降级 → 不变；非 hold → 归零；浮亏 → 归零。"""
    session, _, _ = _make_session(action="hold", profit_rule=_pg_rule())
    profitable = {"returnPct": 0.02}
    losing = {"returnPct": -0.01}

    session._update_profit_hold_streak("hold", None, profitable)
    session._update_profit_hold_streak("hold", None, profitable)
    assert session._guard.hold_streak == 2
    session._update_profit_hold_streak("hold", "llm_error", profitable)
    assert session._guard.hold_streak == 2               # 降级轮不变
    session._update_profit_hold_streak("hold", None, losing)
    assert session._guard.hold_streak == 0
    session._update_profit_hold_streak("hold", None, profitable)
    session._update_profit_hold_streak("open_long", None, profitable)
    assert session._guard.hold_streak == 0               # 已展示交易意愿 ⇒ 归零


# ============ F2 冷却期 + F4 峰值浮盈回填（ai-trade-profit-protection-adaptive §2.2/§2.4）============
async def test_protective_exit_starts_cooldown_and_wires_three_carriers():
    """保护层**确实平掉一笔**后开启冷却，并同时出现在决策 payload / round_start 轨迹 / status 三载体。"""
    await init_db()
    rule = _pg_rule(cooldown_s=180.0)
    session, _, _ = _make_session(action="hold", profit_rule=rule)
    try:
        _open_long(session)
        peak = _pg_peak_px(0.10)
        _arm_peak(session, peak)
        session._last_live_px = _pg_trail_px(peak, rule)

        await session._maybe_profit_exit(_START_TS)

        assert session._guard.cooldown is not None
        assert session._guard.cooldown.side == "long"
        assert session._guard.cooldown.reason_code == "profit_guard_trailing"
        payload = session._build_decision_payload(1, 0, _PG_ENTRY, [], _CANDLE_WAKE)
        view = payload["profitGuardCooldown"]
        assert view["blockedSide"] == "long" and view["scope"] == "same_side"
        assert 0 < view["remainingSeconds"] <= 180
        assert session._status_payload()["profitGuardCooldown"]["blockedSide"] == "long"
        from app.ai_auto.trace import new_round_trace
        assert new_round_trace(1, 0, _PG_ENTRY, payload, [])["profitGuardCooldown"] == view
    finally:
        await dispose_engine()


async def test_cooldown_gate_sits_between_base_rule_and_consensus():
    """门禁落点：冷却把同向开仓降级为 hold（铁律后、共识前）；`close` 与反向开仓不受影响。"""
    await init_db()
    rule = _pg_rule(cooldown_s=180.0)
    session, _, _ = _make_session(action="hold", profit_rule=rule)
    try:
        from app.ai_auto.decider import TradeDecision
        from app.ai_auto.profit_cooldown import CooldownState

        session._guard.cooldown = CooldownState(
            until_wall=time.time() + 120.0, side="long",
            reason_code="profit_guard_trailing", label="移动止盈")

        opened = TradeDecision(action="open_long", size_fraction=0.5, confidence=0.6, reason="x")
        degraded, reason = session._apply_profit_cooldown_gate(opened, None)
        assert degraded.action == "hold" and reason.startswith("profit_cooldown(")

        closing = TradeDecision(action="close", size_fraction=None, confidence=None, reason="x")
        passed, none_reason = session._apply_profit_cooldown_gate(closing, None)
        assert passed is closing and none_reason is None

        # 已有降级码时先执行者胜出（铁律的原因不被冷却覆盖）。
        _, kept = session._apply_profit_cooldown_gate(opened, "base_rule_ma_band(pctB=0.42)")
        assert kept == "base_rule_ma_band(pctB=0.42)"
    finally:
        await dispose_engine()


async def test_expired_cooldown_pushes_one_more_status():
    """冷却到期主动补推一次 status（`profitGuardCooldown: null`）——不推则前端 chip 会一直挂着。"""
    await init_db()
    session, _, _ = _make_session(action="hold", profit_rule=_pg_rule(cooldown_s=180.0))
    try:
        from app.ai_auto.profit_cooldown import CooldownState

        session._guard.cooldown = CooldownState(
            until_wall=time.time() - 1.0, side="long",
            reason_code="profit_guard_trailing", label="移动止盈")
        q = session.subscribe()

        await session._expire_profit_cooldown()

        events = [e for e in _drain(q) if e["type"] == "status"]
        assert len(events) == 1
        assert events[0]["data"]["profitGuardCooldown"] is None
        assert session._guard.cooldown is None

        await session._expire_profit_cooldown()          # 幂等：已清空则不再推
        assert _drain(q) == []
    finally:
        await dispose_engine()


async def test_close_trade_carries_peak_pnl_read_and_consumed():
    """T-4（F4）：close 成交带 `peakPnlUsd` 且**读取即消费**——第二笔不得继承第一笔的峰值。"""
    await init_db()
    session, _, _ = _make_session(action="hold", profit_rule=_pg_rule())
    try:
        _open_long(session)
        _arm_peak(session, _pg_peak_px(0.10))
        assert session._guard.last_peak_pnl_usd is not None

        first = {"ts": _START_TS, "side": "close", "price": _PG_ENTRY, "qty": 1.0,
                 "fee": 0.0, "pnl": 0.1, "positionAfter": 0.0, "reason": "x"}
        second = dict(first, ts=_START_TS + 1)
        await session._dispatch([{"type": "trade", "data": first},
                                 {"type": "trade", "data": second}])

        assert first["peakPnlUsd"] is not None and first["peakPnlUsd"] > 0
        assert second["peakPnlUsd"] is None              # 绝不继承，也绝不补 0
    finally:
        await dispose_engine()


async def test_profit_guard_status_message_is_derived_from_pnl_sign():
    """§六.1b / P0-1：保护性平仓的 status 文案由 pnl 符号派生，不无条件写「锁定利润」。"""
    await init_db()
    rule = _pg_rule()
    session, _, _ = _make_session(action="hold", profit_rule=rule)
    try:
        _open_long(session)
        q = session.subscribe()
        peak = _pg_peak_px(0.10)
        _arm_peak(session, peak)
        session._last_live_px = _pg_trail_px(peak, rule)

        await session._maybe_profit_exit(_START_TS)

        messages = [e["data"].get("message") for e in _drain(q) if e["type"] == "status"]
        assert any(m and "锁定利润" in m for m in messages)   # 本例确为正浮盈出场
    finally:
        await dispose_engine()


# ============ 承诺退出线实盘接线（ai-trade-profit-protection-commitment C1/C2）============
async def test_session_records_commitment_only_for_hold_with_value():
    """C1 §2.2.4：_record_profit_commitment 仅 hold + 有值时记进运行时棘轮；非 hold / 无值 ⇒ 不记。"""
    await init_db()
    rule = _pg_rule(commit_enabled=True, atr_floor_mult=0.0)
    session, _, _ = _make_session(action="hold", profit_rule=rule)
    try:
        _open_long(session)
        peak = _pg_peak_px(0.20)
        _arm_peak(session, peak)
        snap = session._refresh_profit_state(time.time())
        assert snap["status"] in ("armed", "breakeven_armed")
        trail = _pg_trail_px(peak, rule)
        proposed = trail + (peak - trail) * 0.5           # 现价下方、更靠近现价的合法承诺线
        session._record_profit_commitment("hold", proposed, snap)
        assert session._guard.committed_exit_px is not None
        before = session._guard.committed_exit_px
        session._record_profit_commitment("open_long", proposed * 1.001, snap)   # 非 hold ⇒ 不动
        assert session._guard.committed_exit_px == before
        session._record_profit_commitment("hold", None, snap)                    # 无值 ⇒ 不动
        assert session._guard.committed_exit_px == before
    finally:
        await dispose_engine()


async def test_session_commitment_exit_closes_with_committed_reason():
    """T-1 实盘：承诺线比移动止盈线更紧时，现价触及承诺线即经**既有** _maybe_profit_exit 平仓（承诺归因）。"""
    await init_db()
    rule = _pg_rule(commit_enabled=True, atr_floor_mult=0.0)
    session, _, _ = _make_session(action="hold", profit_rule=rule)
    try:
        _open_long(session)
        q = session.subscribe()
        peak = _pg_peak_px(0.20)
        _arm_peak(session, peak)
        trail = _pg_trail_px(peak, rule)
        committed = trail + (peak - trail) * 0.5
        snap = session._refresh_profit_state(time.time())
        session._record_profit_commitment("hold", committed, snap)
        session._last_live_px = committed - (committed - trail) * 0.5   # 触及承诺线、未触及更低的 trail
        await session._maybe_profit_exit(_START_TS)
        trades = [e["data"] for e in _drain(q) if e["type"] == "trade"]
        assert [t["side"] for t in trades] == ["close"]
        assert trades[0]["reason"] == "profit_guard_committed_exit"
        assert session.profit_guard_exits == 1
    finally:
        await dispose_engine()


async def test_session_commitment_resets_on_reopen_same_side():
    """T-6 实盘（[P0-1]）：保护平仓 → 同向重开后，旧承诺线归零，绝不据它强平新仓。"""
    await init_db()
    rule = _pg_rule(commit_enabled=True, atr_floor_mult=0.0)
    session, _, _ = _make_session(action="hold", profit_rule=rule)
    try:
        _open_long(session)
        peak = _pg_peak_px(0.20)
        _arm_peak(session, peak)
        trail = _pg_trail_px(peak, rule)
        committed = trail + (peak - trail) * 0.5
        snap = session._refresh_profit_state(time.time())
        session._record_profit_commitment("hold", committed, snap)
        session._last_live_px = committed - (committed - trail) * 0.5
        await session._maybe_profit_exit(_START_TS)
        assert session.profit_guard_exits == 1
        assert session._guard.committed_exit_px is None            # clear_state 三清
        # 同向重开：seq 前进（close 成交驱动），新 epoch ⇒ 承诺线仍为 None
        _open_long(session)
        session._refresh_profit_state(time.time())
        assert session._guard.committed_exit_px is None
    finally:
        await dispose_engine()


async def _no_trade_fee(_inst_id):
    """费率取数桩：恒不可用 ⇒ 回落配置常数（与本文件既有断言的费率口径一致）。"""
    return None


async def _no_funding_refresh(_now_wall):
    """公共资金费刷新同样脱网，避免后台请求及跨事件循环连接残留。"""
    return False


async def _no_position_history():
    """持仓历史取数桩：恒空 ⇒ 对账 status="unavailable"，绝不触网。"""
    return []
