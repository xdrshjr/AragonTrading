"""唤醒感知上下文的会话级贯通单测（ai-trade-wake-aware-context §6.1 T-12~T-14/T-24/T-25）。

覆盖：定时轮注入战术段与唤醒原因、`bar_advanced` 复现（in-flight 丢轮后的首次研判）、`wakeKind`
落 explain 归因、连续跳过上限兜底与其计数口径、guard status 单一真源、决策瞬刻双钟。

行情 / OKX / LLM 全程桩驱动；定时轮以 `asyncio.create_task` 运行，断言前须 await `_decision_task`。
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from app.agent.llm.base import StreamEvent
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.session import AiTradeSession
from app.ai_auto.wake_reason import WakeReadings
from app.ai_auto.wake_runtime import WakeRuntime
from app.config import Settings
from app.db.session import dispose_engine, init_db
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.okx.accounts import AccountCredentials
from app.okx.types import BAR_SECONDS, Candle
from app.paper.strategy_factory import PaperStrategySpec
from app.strategies.base import BaseStrategy, StrategyMeta

_START_TS = 1_700_000_000
_BAR = "1H"
_STEP = BAR_SECONDS[_BAR]


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_wake_ctx", name="Stub", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


class _StubLLM:
    async def stream(self, *, system, messages, tools):
        if tools:
            args = {"action": "hold", "reason": "r", "confidence": 0.7}
            yield StreamEvent("tool_use_start", id="t", name="submit_trade_decision")
            yield StreamEvent("tool_use_args_delta", id="t",
                              partial_json=json.dumps(args, ensure_ascii=False))
            yield StreamEvent("message_done", stop_reason="tool_use")
        else:
            yield StreamEvent("text_delta", text="摘要")
            yield StreamEvent("message_done", stop_reason="end")


def _candle(idx: int, price: float = 30000.0) -> Candle:
    return Candle(ts=_START_TS + idx * _STEP, o=price, h=price + 5, l=price - 5,
                  c=price, vol=100.0, confirm=True)


def _fresh_tactical_rows(bar: str, rows: int = 120) -> list[Candle]:
    """生成一批**贴近当前墙钟**的确认根：定时轮的 as_of 是墙钟，用陈年数据会被因果裁剪全部丢掉。"""
    secs = BAR_SECONDS[bar]
    last_open = (int(time.time()) // secs) * secs - secs
    return [Candle(ts=last_open - (rows - 1 - i) * secs, o=30000.0 + i, h=30010.0 + i,
                   l=29990.0 + i, c=30000.0 + i * 2.0, vol=100.0 + i, confirm=True)
            for i in range(rows)]


def _make_session(*, wake_interval_s: float = 120.0, settings: Settings | None = None):
    settings = settings or Settings(ai_trade_wake_skip_unchanged_pct=0.0)

    async def place_order_fn(order):
        return {"ok": True, "ordId": "x", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        return []

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(), ct_val=0.01,
        lot_sz=0.01, min_sz=0.01, settings=settings,
        place_order_fn=place_order_fn, close_position_fn=close_position_fn,
        positions_fn=positions_fn)
    decider = AiTradeDecider(_StubLLM(), "sys", context_refresh_rounds=10, settings=settings)
    spec = PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})
    session = AiTradeSession("aitrade_wakectx", 1, [spec], "BTC-USDT-SWAP", _BAR, executor,
                             decider, priming=False, priming_bars=10,
                             context_refresh_rounds=10, wake_interval_s=wake_interval_s)

    async def stub_candles(bar, limit):
        return _fresh_tactical_rows(bar) if bar in session._window.tactical_bars else []

    session._window._safe_get_candles = stub_candles
    return session


async def _settle(session) -> None:
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
    session._last_decision_started_wall = time.time() - (session.wake_interval_s + 5)


# ---------- T-12：会话贯通 ----------
async def test_interval_round_carries_wake_reason_and_fresh_tactical_evidence():
    """定时轮的 decision 事件必须同时带 `wake.kind` 与非空 `tacticalContext.frames`。

    这正是本特性的全部目的：过去定时轮拿到的证据与上一轮**逐字节相同**，模型只能反复 hold。
    """
    await init_db()
    session = _make_session()
    try:
        assert session._window.tactical_bars == ("1m", "5m")   # 1H 执行 + 2 分钟唤醒
        await session._window.prefill_tactical()
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        _drain(q)
        _elapse_timer(session)
        await session._maybe_interval_wake()
        await _settle(session)

        decision = [e for e in _drain(q) if e["type"] == "decision"][-1]["data"]
        assert decision["trigger"] == "interval"              # 既有 wire 语义逐字不变
        assert decision["wake"]["kind"] in {
            "bar_advanced", "price_breakout", "heartbeat", "position_watch"}
        context = decision["tacticalContext"]
        assert [f["bar"] for f in context["frames"]] == ["5m", "1m"]
        assert context["executionBar"] == _BAR
        assert all(f["stale"] is False for f in context["frames"])   # P0-1：墙钟口径的决策瞬刻
    finally:
        await dispose_engine()


async def test_decision_as_of_ts_has_exactly_one_source_per_wake_path():
    """K 线轮 = 该根收盘时刻；定时轮 = `ts` 本身（已是墙钟）。"""
    await init_db()
    session = _make_session()
    try:
        assert session._decision_as_of_ts(_START_TS, {"trigger": "candle"}) == _START_TS + _STEP
        assert session._decision_as_of_ts(_START_TS, {"trigger": "interval"}) == _START_TS
    finally:
        await dispose_engine()


# ---------- T-13：bar_advanced 复现 ----------
async def test_dropped_candle_round_is_visible_and_the_next_wake_is_bar_advanced():
    """in-flight 丢轮 ⇒ skipped 事件带 `wake.dropped`；随后的定时轮判为 `bar_advanced` 且全量披露。"""
    await init_db()
    session = _make_session()
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        _drain(q)

        session._deciding = True                       # 模拟上一轮 LLM 仍在跑
        await session.feed_candle(_candle(1))
        skipped = [e for e in _drain(q) if e["type"] == "decision"][-1]["data"]
        assert skipped["executed"]["reason"] == "decision_in_flight"
        assert skipped["wake"] == {"kind": "candle_close", "trigger": "candle", "dropped": True}

        session._deciding = False                      # 放开后由定时唤醒补看这根 K 线
        _elapse_timer(session)
        await session._maybe_interval_wake()
        await _settle(session)
        decision = [e for e in _drain(q) if e["type"] == "decision"][-1]["data"]
        assert decision["wake"]["kind"] == "bar_advanced"
        assert decision["wake"]["barAdvanced"] is True
    finally:
        await dispose_engine()


async def test_candle_rounds_get_full_execution_disclosure():
    """K 线轮的 kind 恒为 `candle_close`（优先级第 1 条），执行周期段全量披露。

    `barAdvanced` 仍如实为 True（首轮无上一根参照），只是它不抢 `candle_close` 的标签位——
    「新 K 线收盘」比「补看新 K 线」更准确地描述了这一轮。
    """
    await init_db()
    session = _make_session()
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))          # 首轮：last_round_bar_index=None
        await _settle(session)
        round_start = next(e["data"] for e in _drain(q)
                           if e["type"] == "agent_trace" and e["data"]["phase"] == "round_start")
        assert round_start["wake"]["kind"] == "candle_close"
        assert round_start["wake"]["barAdvanced"] is True
        # 证据不丢：首轮无上一轮参考价 ⇒ moved() fail-open 也如实记进 alsoTrue
        assert round_start["wake"]["alsoTrue"] == ["bar_advanced", "price_breakout"]
        assert round_start["contextDisclosure"] == "full"
    finally:
        await dispose_engine()


async def test_repeat_rounds_of_the_same_bar_are_compressed():
    await init_db()
    session = _make_session()
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        _drain(q)
        _elapse_timer(session)
        await session._maybe_interval_wake()           # 同一根的第 2 次决策
        await _settle(session)
        round_start = next(e["data"] for e in _drain(q)
                           if e["type"] == "agent_trace" and e["data"]["phase"] == "round_start")
        assert round_start["contextDisclosure"] == "repeat"
    finally:
        await dispose_engine()


# ---------- T-14：归因 ----------
async def test_wake_kind_lands_only_on_ai_originated_fills():
    """AI 自己的开/平仓带 `wakeKind`；利润保护平仓**不带**——否则「按唤醒来源切胜率」整体失真。"""
    await init_db()
    session = _make_session()
    try:
        session._wake.note_round_start({"kind": "price_breakout"}, px=30000.0, bar_index=0,
                                       as_of_ts=_START_TS, now_wall=time.time())
        atomic = {"ts": _START_TS, "side": "long", "explain": {"kind": "atomic", "meta": {}}}
        session._inject_explain_meta(atomic)
        assert atomic["explain"]["meta"]["wakeKind"] == "price_breakout"

        reverse = {"ts": _START_TS, "side": "close", "explain": {"kind": "reverse", "meta": {}}}
        session._inject_explain_meta(reverse)
        assert reverse["explain"]["meta"]["wakeKind"] == "price_breakout"

        guard = {"ts": _START_TS, "side": "close", "explain": {"kind": "profit_guard", "meta": {}}}
        session._inject_explain_meta(guard)
        assert "wakeKind" not in guard["explain"]["meta"]
    finally:
        await dispose_engine()


async def test_wake_kind_is_absent_when_the_reason_switch_is_off():
    await init_db()
    session = _make_session(settings=Settings(ai_trade_wake_reason_enabled=False))
    try:
        session._wake.note_round_start({"kind": "price_breakout"}, px=30000.0, bar_index=0,
                                       as_of_ts=_START_TS, now_wall=time.time())
        trade = {"ts": _START_TS, "side": "long", "explain": {"kind": "atomic", "meta": {}}}
        session._inject_explain_meta(trade)
        assert "wakeKind" not in trade["explain"]["meta"]
    finally:
        await dispose_engine()


async def test_all_three_switches_off_leaves_the_payload_free_of_new_keys():
    """A-6 回退：三总闸全关 ⇒ 四个新键**整键缺席**（不是 None），payload 冻结断言逐字节回退。"""
    await init_db()
    session = _make_session(settings=Settings(
        ai_trade_tactical_context=False, ai_trade_wake_reason_enabled=False,
        ai_trade_progressive_disclosure=False))
    try:
        payload = session._build_decision_payload(
            1, _START_TS, 30000.0, [], {"trigger": "candle", "kind": "candle_close"})
        for key in ("wake", "tacticalContext", "tacticalTier", "contextDisclosure"):
            assert key not in payload
        assert payload["trigger"] == "candle"           # 既有二值 wire 逐字不变
    finally:
        await dispose_engine()


# ---------- T-24：连续跳过上限 ----------
def _runtime(**settings_kwargs) -> WakeRuntime:
    settings = Settings(ai_trade_wake_skip_unchanged_pct=0.0005, **settings_kwargs)
    return WakeRuntime(_BAR, 180.0, settings)


def _steady_readings() -> WakeReadings:
    return WakeReadings(trigger="interval", position_side="flat", has_resting=False,
                        bar_index=3, px=30000.0)


def test_skip_streak_ceiling_forces_one_round_through():
    runtime = _runtime(ai_trade_wake_max_skip_streak=5)
    runtime.note_round_start({"kind": "candle_close"}, px=30000.0, bar_index=3,
                             as_of_ts=_START_TS, now_wall=1000.0)
    for expected_streak in range(5):
        wake = runtime.classify(_steady_readings(), now_wall=1000.0 + expected_streak)
        assert wake["kind"] == "heartbeat"
        assert wake["skipStreak"] == expected_streak
        assert runtime.should_skip(wake) is True
        runtime.note_skip(True)
    sixth = runtime.classify(_steady_readings(), now_wall=1100.0)
    assert sixth["skipStreak"] == 5
    assert runtime.should_skip(sixth) is False           # 强制放行一轮（饿死兜底）
    assert sixth["kind"] == "heartbeat"                  # kind 仍如实，不新造一种


def test_uncounted_skips_never_advance_the_streak():
    """口径易踩点：若按 5 秒 tick 裸计数，3 分钟唤醒周期内会被打到 36，兜底会退化成每 25 秒一叫。"""
    runtime = _runtime(ai_trade_wake_max_skip_streak=5)
    runtime.note_round_start({"kind": "candle_close"}, px=30000.0, bar_index=3,
                             as_of_ts=_START_TS, now_wall=1000.0)
    for _ in range(36):
        runtime.note_skip(False)                        # `_count_wake_skip` 被节流挡下的那些 tick
    assert runtime.skip_streak == 0
    assert runtime.should_skip(runtime.classify(_steady_readings(), now_wall=1000.0)) is True


def test_any_decision_round_resets_the_streak():
    runtime = _runtime(ai_trade_wake_max_skip_streak=5)
    for _ in range(3):
        runtime.note_skip(True)
    assert runtime.skip_streak == 3
    runtime.note_round_start({"kind": "price_breakout"}, px=30000.0, bar_index=3,
                             as_of_ts=_START_TS, now_wall=1000.0)
    assert runtime.skip_streak == 0


def test_guard_falls_back_verbatim_when_the_reason_switch_is_off():
    """总闸关闭 ⇒ 逐字回退既有 `should_skip_interval_wake`（G-4 的活证据）。"""
    runtime = _runtime(ai_trade_wake_reason_enabled=False)
    runtime.note_round_start({"kind": "candle_close"}, px=30000.0, bar_index=3,
                             as_of_ts=_START_TS, now_wall=1000.0)
    unchanged = runtime.classify(_steady_readings(), now_wall=1000.0)
    assert runtime.should_skip(unchanged) is True                  # 空仓 + 同根 + 价未动
    moved_away = runtime.classify(
        WakeReadings(trigger="interval", position_side="flat", has_resting=False,
                     bar_index=3, px=30100.0), now_wall=1000.0)
    assert runtime.should_skip(moved_away) is False


def test_both_thresholds_zero_disables_the_guard_entirely():
    runtime = WakeRuntime(_BAR, 180.0, Settings(ai_trade_wake_skip_unchanged_pct=0.0,
                                                ai_trade_wake_move_atr_mult=0.0))
    runtime.note_round_start({"kind": "candle_close"}, px=30000.0, bar_index=3,
                             as_of_ts=_START_TS, now_wall=1000.0)
    wake = runtime.classify(_steady_readings(), now_wall=1000.0)
    assert runtime.should_skip(wake) is False


# ---------- T-25：guard status 单一真源 ----------
def test_guard_status_is_read_back_from_the_single_entry_point():
    runtime = _runtime()
    runtime.note_guard_status("armed")
    runtime.note_atr(120.0)
    wake = runtime.classify(
        WakeReadings(trigger="interval", position_side="long", has_resting=False,
                     bar_index=3, px=30000.0), now_wall=1000.0)
    assert wake["guardStatus"] == "armed"
    assert wake["lastGuardStatus"] is None                # 尚无决策轮 ⇒ 上一轮无快照
    assert wake["kind"] == "bar_advanced"                 # 首轮如实（last_round_bar_index is None）

    runtime.note_round_start(wake, px=30000.0, bar_index=3, as_of_ts=_START_TS, now_wall=1000.0)
    runtime.note_guard_status("breakeven_armed")
    later = runtime.classify(
        WakeReadings(trigger="interval", position_side="long", has_resting=False,
                     bar_index=3, px=30000.0), now_wall=1180.0)
    assert later["lastGuardStatus"] == "armed"            # 冻结的正是分类当时所见
    assert later["kind"] == "profit_tier"                 # idle→armed→breakeven_armed 即跨档


def test_reference_uses_the_decision_instant_clock_not_the_wall_clock():
    runtime = _runtime()
    assert runtime.last_reference is None                  # 首轮无参照
    runtime.note_atr(80.0)
    runtime.note_round_start({"kind": "candle_close"}, px=30000.0, bar_index=3,
                             as_of_ts=_START_TS + 999, now_wall=time.time())
    assert runtime.last_reference == {"px": 30000.0, "asOfTs": _START_TS + 999, "atrPx": 80.0}


def test_tactical_bars_text_matches_the_bars_actually_injected():
    """系统 prompt「说的」与 payload「给的」必须是同一批周期（同源）。"""
    runtime = _runtime()
    assert runtime.tactical_bars == ("1m", "5m")
    assert runtime.tactical_bars_text == "5m → 1m"
    off = WakeRuntime(_BAR, 180.0, Settings(ai_trade_tactical_context=False))
    assert off.tactical_bars == ()
    assert off.tactical_bars_text == ""


# ---------- 计数节流（会话侧口径）----------
async def test_session_skip_counter_is_throttled_to_one_per_wake_period():
    await init_db()
    session = _make_session(wake_interval_s=180.0, settings=Settings())
    try:
        session._last_decision_started_wall = 0.0
        now = time.time()
        assert session._count_wake_skip(now) is True
        for tick in range(1, 36):                       # 5 秒 tick 连打 35 次
            assert session._count_wake_skip(now + tick * 5) is False
        assert session.wake_skips == 1
        assert session._count_wake_skip(now + 200) is True
        assert session.wake_skips == 2
    finally:
        await dispose_engine()


@pytest.mark.parametrize("side", ["flat", "long"])
async def test_monitor_tick_refreshes_tactical_before_interval_wake(side, monkeypatch):
    """刷新必须排在唤醒之前——否则定时轮拿到的是上一分钟的战术数据，特性在最该生效的路径上空转。"""
    await init_db()
    session = _make_session()
    try:
        order: list[str] = []

        async def track_refresh(now_ts, now_wall):
            order.append("refresh")

        async def track_wake():
            order.append("wake")

        monkeypatch.setattr(session._window, "maybe_refresh_tactical", track_refresh)
        monkeypatch.setattr(session, "_maybe_interval_wake", track_wake)
        session.executor.projected = side
        await session._monitor_tick()
        assert order == ["refresh", "wake"]
    finally:
        await dispose_engine()
