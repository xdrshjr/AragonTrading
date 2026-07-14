"""Agent 墙钟定时唤醒单测（ai-auto-trading-wake-interval §6.1）。

覆盖第三触发源「墙钟定时唤醒」的触发 / 复用 / 跳过 / 关闭 / 计时器合流 + 后端夹紧解析：
定时到点用「最近确认根信号板 + 当前实时价」起一轮标准决策（trigger="interval"）；in-flight /
行情陈旧 / 关闭 / 尚无信号板一律不唤醒；任一轮决策开始即刷新计时器（K 线唤醒与定时唤醒合流去重）。

行情 / OKX / LLM 全程桩驱动（直接注入 feed_candle + 直调 _maybe_interval_wake + 回拨墙钟），
铁律：占位假凭证。定时轮以 asyncio.create_task 运行，断言前须 await session._decision_task（RN-5）。
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from app.agent.llm.base import StreamEvent
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.session import AiTradeSession
from app.api.ai_auto import _resolve_wake_interval_s
from app.config import Settings
from app.db.session import dispose_engine, init_db
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.okx.accounts import AccountCredentials
from app.okx.types import Candle
from app.paper.strategy_factory import PaperStrategySpec
from app.strategies.base import BaseStrategy, StrategyMeta

_START_TS = 1_700_000_000
_STEP = 3600


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_ai_wake", name="Stub", description="t",
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


def _make_session(*, action="hold", wake_interval_s=0.0, priming=False, settings=None):
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

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(), ct_val=0.01,
        lot_sz=0.01, min_sz=0.01, settings=settings,
        place_order_fn=place_order_fn, close_position_fn=close_position_fn,
        positions_fn=positions_fn)
    decider = AiTradeDecider(_StubLLM(action), "sys", context_refresh_rounds=10,
                             settings=settings)
    spec = PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})
    session = AiTradeSession("aitrade_wake", 1, [spec], "BTC-USDT-SWAP", "1H", executor,
                             decider, priming=priming, priming_bars=10,
                             context_refresh_rounds=10, wake_interval_s=wake_interval_s)

    async def no_htf_data(*_args, **_kwargs):
        return []

    session._window._safe_get_candles = no_htf_data
    return session


async def _settle(session) -> None:
    """等待本轮后台决策任务完成（决策以 asyncio.create_task 运行，RN-5：断言前须 await）。"""
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


def _elapse_timer(session) -> None:
    """把决策计时器回拨到「已超过唤醒周期」，令下一次 _maybe_interval_wake 判定到点（无需 sleep）。"""
    session._last_decision_started_wall = time.time() - (session.wake_interval_s + 5)


async def test_interval_wake_triggers_decision_between_candles():
    """wake_interval_s=120：一根确认根决策后回拨计时器 → _maybe_interval_wake 起一轮 interval 决策。"""
    await init_db()
    session = _make_session(action="hold", wake_interval_s=120)
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        assert session.rounds_completed == 1
        _drain(q)                                  # 清掉 K 线轮事件，只看定时轮
        _elapse_timer(session)
        await session._maybe_interval_wake()
        await _settle(session)                     # 只 create_task 即返回，须 await 决策任务再断言
        assert session.rounds_completed == 2
        decision = [e for e in _drain(q) if e["type"] == "decision"][-1]["data"]
        assert decision["trigger"] == "interval"
    finally:
        await dispose_engine()


async def test_interval_wake_reuses_last_board_and_live_px():
    """定时轮用最近确认根信号板 + 当前实时价：round_start.lastPx == _last_live_px、signalBoard 同上一根。"""
    await init_db()
    session = _make_session(action="hold", wake_interval_s=120)
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        _drain(q)
        board_ref = session._last_board
        session._last_live_px = 31234.0            # 两根 K 线之间的最新实时价
        _elapse_timer(session)
        await session._maybe_interval_wake()
        await _settle(session)
        events = _drain(q)
        round_start = next(e["data"] for e in events
                           if e["type"] == "agent_trace" and e["data"]["phase"] == "round_start")
        decision = [e for e in events if e["type"] == "decision"][-1]["data"]
        assert round_start["lastPx"] == pytest.approx(31234.0)
        assert round_start["trigger"] == "interval"
        assert decision["signalBoard"] == [session._view_slim(v) for v in board_ref]
    finally:
        await dispose_engine()


async def test_interval_wake_skipped_when_deciding():
    """_deciding=True（上一轮在飞）→ 定时唤醒不起新任务、不并发（in-flight 守卫复用）。"""
    await init_db()
    session = _make_session(action="hold", wake_interval_s=120)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        task_before = session._decision_task
        session._deciding = True                   # 模拟上一轮仍在进行
        _elapse_timer(session)
        await session._maybe_interval_wake()
        assert session._decision_task is task_before  # 未起新任务
        assert session.rounds_completed == 1
    finally:
        await dispose_engine()


async def test_interval_wake_skipped_when_stale():
    """_stale_degraded=True（行情连接异常）→ 定时唤醒跳过（实时价不可信，交由交易所侧 OCO 保护）。"""
    await init_db()
    session = _make_session(action="hold", wake_interval_s=120)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        session._stale_degraded = True
        _elapse_timer(session)
        await session._maybe_interval_wake()
        assert session.rounds_completed == 1       # 未唤醒
    finally:
        await dispose_engine()


async def test_interval_wake_disabled_when_zero():
    """wake_interval_s=0（默认关闭）→ 时间流逝也不唤醒（零回归退出口）。"""
    await init_db()
    session = _make_session(action="hold", wake_interval_s=0.0)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        session._last_decision_started_wall = time.time() - 10_000  # 早已「到点」
        await session._maybe_interval_wake()
        assert session.rounds_completed == 1
        assert session._wake_interval_minutes() is None            # 关闭态回执 None（RN-7）
    finally:
        await dispose_engine()


async def test_candle_decision_resets_interval_timer():
    """确认根决策后计时器被刷新为近值 → 紧邻的 _maybe_interval_wake 不立即触发（合流去重）。"""
    await init_db()
    session = _make_session(action="hold", wake_interval_s=120)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        # 不回拨计时器：feed_candle 已把 _last_decision_started_wall 刷到 ~now，距唤醒周期尚远。
        await session._maybe_interval_wake()
        assert session.rounds_completed == 1
    finally:
        await dispose_engine()


async def test_no_board_before_first_candle_no_wake():
    """priming=False、_last_board is None（尚无信号板）→ 定时唤醒不开工（边角场景，待第一根确认根）。"""
    await init_db()
    session = _make_session(action="hold", wake_interval_s=120, priming=False)
    try:
        assert session._last_board is None
        session._last_decision_started_wall = time.time() - 10_000
        await session._maybe_interval_wake()
        assert session.rounds_completed == 0
    finally:
        await dispose_engine()


def test_resolve_wake_interval_clamps():
    """_resolve_wake_interval_s：None→默认*60；0→0；0.1→floor_s；9999→ceil_min*60；正常值原样*60。"""
    settings = Settings()   # default=2.0min / floor=30s / ceil=240min
    assert _resolve_wake_interval_s(None, settings) == pytest.approx(120.0)
    assert _resolve_wake_interval_s(0, settings) == 0.0
    assert _resolve_wake_interval_s(0.1, settings) == pytest.approx(30.0)   # 6s < floor 30s → 夹到 30
    assert _resolve_wake_interval_s(9999, settings) == pytest.approx(14400.0)  # > ceil 240min → 夹到 4h
    assert _resolve_wake_interval_s(5, settings) == pytest.approx(300.0)
