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
    # 脱网守门（cost-truth-and-net-pnl I-1）：轴 A / 轴 C 的两条签名私有 GET 由 `CostRuntime` 在
    # 盯市 tick 的后台任务里发起。直接构造会话的用例带的是**假凭证**，不桩掉就会真的打 OKX——
    # 本文件承诺的是脱网单测，且真实往返会让每个 tick 多花数秒。
    session._cost._fetch_fee_rate = _no_trade_fee
    session._cost._fetch_position_history = _no_position_history
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


@pytest.fixture(autouse=True)
def _reset_preview_concurrency():
    """预演并发计数是**模块级**进程共享态：用例之间必须归零，否则前一条的残留会静默改变后一条
    的行为（拿不到额度就跳过本 tick），把确定性对拍变成掷骰子。"""
    from app.ai_auto import signal_preview
    signal_preview._inflight_global = 0
    yield
    signal_preview._inflight_global = 0


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
        # 轴 A：丢弃事件自带待办快照，「这根 K 线的首次研判被推迟了几次、等了多久」当场可见。
        assert skipped["wake"]["kind"] == "candle_close"
        assert skipped["wake"]["dropped"] is True
        assert skipped["wake"]["backlog"]["droppedBars"] == 1

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


# ============================ 轴 A：唤醒补跑（T-3/T-5/A-1/A-6）============================
async def test_a_dropped_bar_is_re_examined_by_a_catchup_round_in_the_same_tick():
    """A-1：在飞期间收线的确认根，在上一轮 `finally` 里立刻被补跑一轮研判。

    这是旧实现完全做不到的场景——那根 K 线的首次研判过去会**永久消失**（定时唤醒关闭时更是如此）。
    """
    await init_db()
    session = _make_session(wake_interval_s=0.0)          # 定时唤醒关闭：补跑是唯一的补看途径
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        _drain(q)

        session._deciding = True                          # 上一轮 LLM 仍在跑
        await session.feed_candle(_candle(1))             # 这根收线了，但研判被丢弃
        session._deciding = False
        session._maybe_catchup_wake()                     # 上一轮 finally 的那一行
        await _settle(session)

        decision = [e for e in _drain(q) if e["type"] == "decision"][-1]["data"]
        assert decision["wake"]["kind"] == "bar_advanced"
        assert decision["wake"]["backlog"]["droppedBars"] == 1
        assert decision["wake"]["backlog"]["chain"] == 1
        assert session._wake.stats()["catchups"] == 1
    finally:
        await dispose_engine()


async def test_stale_feed_blocks_the_catchup_round_and_keeps_the_backlog(monkeypatch):
    """G-6（discipline-preset-and-integrity D-4）：行情陈旧期补跑不起轮，**且待办必须保留**。

    三条唤醒源里此前只有补跑漏了 `_stale_degraded` 守卫（`_maybe_interval_wake`/`_preview_ready`
    都有），于是「会话对外宣告已降级停摆、第三条却在照常花真钱」。两条断言缺一不可：
    ① 陈旧期不起轮（安全）；② `WakeBacklog` 的待办数不减（可恢复）——守卫若写进 `plan_catchup`
    内部，三条分支都会先 `consume()` 掉待办，行情恢复后那根 K 线就永远补不回来了。
    """
    await init_db()
    session = _make_session(wake_interval_s=0.0)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)

        session._deciding = True                          # 上一轮在飞
        await session.feed_candle(_candle(1))             # 这根收线被丢弃 ⇒ 记一笔待办
        session._deciding = False
        pending_before = session._wake.stats()["drops"]
        assert pending_before == 1

        session._stale_degraded = True                    # 行情断流：另外两条唤醒源此刻都已停
        session._maybe_catchup_wake()
        await _settle(session)
        assert session._deciding is False                 # ① 没有起轮
        assert session._decision_task is None or session._decision_task.done()
        assert session._wake.stats()["catchups"] == 0     # ② 待办没被消耗
        assert session._wake.stats()["drops"] == pending_before

        session._stale_degraded = False                   # 行情恢复 ⇒ 那根 K 线仍补得回来
        session._maybe_catchup_wake()
        await _settle(session)
        assert session._wake.stats()["catchups"] == 1
    finally:
        await dispose_engine()


async def test_catchup_prompt_carries_the_backlog_clause():
    """A-1：补跑轮的 prompt 首行必须如实说明「丢了什么、等了多久」。"""
    from app.ai_auto.wake_text import format_wake_for_prompt
    await init_db()
    session = _make_session(wake_interval_s=0.0)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        session._deciding = True
        await session.feed_candle(_candle(1))
        session._deciding = False
        wake = session._wake.plan_catchup(session._wake_readings("interval", 30000.0),
                                          now_wall=time.time())
        assert "补跑" in format_wake_for_prompt(wake)
        assert "1 根 K 线收线未被研判" in format_wake_for_prompt(wake)
    finally:
        await dispose_engine()


async def test_catchup_never_starts_a_round_after_the_session_stopped():
    """T-5：`stop()` 取消决策任务同样会跑 `finally`——绝不能在 flatten 之后凭空再开一轮真实资金决策。"""
    await init_db()
    session = _make_session(wake_interval_s=0.0)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        session._deciding = True
        await session.feed_candle(_candle(1))
        session._deciding = False
        session.state = "stopped"
        session._decision_task = None
        session._maybe_catchup_wake()
        assert session._decision_task is None and session._deciding is False
    finally:
        await dispose_engine()


async def test_interval_drops_are_counted_and_pushed_instead_of_vanishing():
    """轴 A：到点却撞上在飞轮 ⇒ 记待办 + 补推一次 status（过去是静默 `return`，无计数、无待办）。"""
    await init_db()
    session = _make_session(wake_interval_s=120.0)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        q = session.subscribe()
        in_flight_task = session._decision_task           # 上一轮的任务句柄（不得被覆盖）
        session._deciding = True
        session._wake._backlog.last_noted_wall = time.time() - 300.0   # 上一轮已开始很久
        for _ in range(3):                                # 三个 5 秒 tick，同一个节流窗
            _elapse_timer(session)
            await session._maybe_interval_wake()
        statuses = [e for e in _drain(q) if e["type"] == "status"]
        assert len(statuses) == 1, "计数被节流 ⇒ 一个唤醒周期至多补推一条 status（绝不刷屏）"
        assert session._wake.stats()["drops"] == 1        # 裸 tick 计数会记成 3
        assert statuses[0]["data"]["wakeDrops"] == 1
        assert session._decision_task is in_flight_task   # 在飞期间绝不并发起第二轮
    finally:
        await dispose_engine()


async def test_catchup_chain_is_capped_and_ceiling_hits_are_counted():
    """T-4：链上限触顶即弃单（绝不背靠背无限烧 LLM），且触顶次数如实计数。"""
    await init_db()
    session = _make_session(wake_interval_s=0.0,
                            settings=Settings(ai_trade_wake_backlog_max_chain=1))
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        # 连续两根确认根都在飞轮里被丢弃：第 1 次补跑成功、第 2 次触顶弃单。
        for idx in (1, 2):
            session._deciding = True
            await session.feed_candle(_candle(idx))
            session._deciding = False
            readings = session._wake_readings("interval", session._current_px())
            planned = session._wake.plan_catchup(readings, now_wall=time.time())
            if idx == 1:
                assert planned is not None and planned["backlog"]["chain"] == 1
                session._wake.note_round_start(planned, px=30000.0, bar_index=idx,
                                               as_of_ts=_START_TS, now_wall=time.time())
            else:
                assert planned is None
        assert session._wake.stats()["catchupCeilingHits"] == 1
        assert session._wake._backlog.pending is False    # 触顶即弃单，不留悬空待办
    finally:
        await dispose_engine()


async def test_catchup_is_skipped_when_the_input_did_not_really_change():
    """T-3：有待办但分类为可跳过的 kind ⇒ 不补跑，**且待办被清空**（不留悬空待办）。"""
    await init_db()
    session = _make_session(wake_interval_s=120.0, settings=Settings())
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        session._wake.note_interval_drop(time.time())     # 只丢了定时唤醒，没有新根
        readings = session._wake_readings("interval", session._current_px())
        assert session._wake.classify(readings, now_wall=time.time())["kind"] == "heartbeat"
        assert session._wake.plan_catchup(readings, now_wall=time.time()) is None
        assert session._wake._backlog.pending is False
    finally:
        await dispose_engine()


async def test_backlog_master_switch_off_counts_but_never_catches_up():
    """总闸关 ⇒ 观测保留、行为逐字回退（只计数不补跑）。"""
    await init_db()
    session = _make_session(wake_interval_s=0.0,
                            settings=Settings(ai_trade_wake_backlog_enabled=False))
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        session._deciding = True
        await session.feed_candle(_candle(1))
        session._deciding = False
        session._decision_task = None
        session._maybe_catchup_wake()
        assert session._decision_task is None
        assert session._wake.stats()["drops"] == 1        # 计数仍在（可观测）
        assert session._wake.stats()["catchups"] == 0
    finally:
        await dispose_engine()


# ============================ A-6：观测自洽 ============================
async def test_status_and_metrics_read_the_same_scheduler_counters():
    """A-6：`status.wakeDrops == aiStats.wakeScheduler.drops`，且 `wakeCatchups <= wakeDrops`。

    两者**同读** `WakeBacklog.total_drops`；没有这条同源纪律，两个数会各自漂到用户面前。
    """
    await init_db()
    session = _make_session(wake_interval_s=0.0)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        session._deciding = True
        await session.feed_candle(_candle(1))
        session._deciding = False
        session._maybe_catchup_wake()
        await _settle(session)
        status = session._status_payload()
        scheduler = session._wake.stats(session._preview.stats())
        assert status["wakeDrops"] == scheduler["drops"] >= 1
        assert status["wakeCatchups"] == scheduler["catchups"]
        assert status["wakeCatchups"] <= status["wakeDrops"]
        assert status["positionWatchSkips"] <= status["wakeSkips"]
        assert set(scheduler) >= {"drops", "catchups", "catchupCeilingHits", "positionWatchSkips",
                                  "signalFlipWakes", "previewTimeouts", "previewDisabled"}
    finally:
        await dispose_engine()


# ============================ 轴 B：持仓轮跳过（T-6/T-7/A-2）============================
def _position_readings() -> WakeReadings:
    return WakeReadings(trigger="interval", position_side="long", has_resting=False,
                        bar_index=3, px=30000.0)


def _armed_runtime(**settings_kwargs) -> WakeRuntime:
    runtime = _runtime(**settings_kwargs)
    runtime.note_guard_status("armed")
    runtime.note_round_start({"kind": "candle_close"}, px=30000.0, bar_index=3,
                             as_of_ts=_START_TS, now_wall=1000.0)
    runtime.note_guard_status("armed")                   # 未跨档 ⇒ 分类落 position_watch
    return runtime


def test_armed_position_watch_rounds_are_skipped_up_to_the_ceiling():
    """A-2：保护层在岗时省下持仓空转轮；触到上限（默认 3）即强制放行一轮。"""
    runtime = _armed_runtime()
    for expected_streak in range(3):
        wake = runtime.classify(_position_readings(), now_wall=1000.0 + expected_streak)
        assert wake["kind"] == "position_watch"
        assert wake["skipStreak"] == expected_streak
        assert runtime.should_skip(wake) is True
        runtime.note_skip(True)
    fourth = runtime.classify(_position_readings(), now_wall=1100.0)
    assert fourth["skipStreak"] == 3
    assert runtime.should_skip(fourth) is False
    assert runtime.position_watch_skips == 3             # 分列计数与 wake_skips 同口径


@pytest.mark.parametrize("status", ["idle", "unavailable", None])
def test_position_watch_is_never_skipped_when_the_guard_is_not_armed(status):
    """`unavailable` 是保护层的 fail-open 降级态，**不代表交易所侧有兜底**——那种局面下 AI 是唯一
    的退出路径，一次都不许跳。"""
    runtime = _armed_runtime()
    runtime.note_guard_status(status)
    wake = runtime.classify(_position_readings(), now_wall=1000.0)
    assert wake["kind"] == "position_watch"
    assert runtime.should_skip(wake) is False
    runtime.note_skip(True)
    assert runtime.position_watch_skips == 0


def test_position_watch_switch_off_falls_back_to_never_skipping():
    runtime = _armed_runtime(ai_trade_wake_skip_position_watch=False)
    wake = runtime.classify(_position_readings(), now_wall=1000.0)
    assert runtime.should_skip(wake) is False


def test_heartbeat_skips_are_not_counted_as_position_watch_skips():
    """分列计数只统计持仓轮：空仓心跳跳过绝不能混进 `positionWatchSkips`（口径必须干净）。"""
    runtime = _runtime()
    runtime.note_round_start({"kind": "candle_close"}, px=30000.0, bar_index=3,
                             as_of_ts=_START_TS, now_wall=1000.0)
    wake = runtime.classify(_steady_readings(), now_wall=1000.0)
    assert runtime.should_skip(wake) is True
    runtime.note_skip(True)
    assert (runtime.skip_streak, runtime.position_watch_skips) == (1, 0)


# ============================ 轴 C：信号驱动唤醒（T-14/T-15/A-7）============================
def _flip_result(net_to: str = "long"):
    """一份「投影净共识 flat→{net_to}」的预演结果（board 直接构造，判据逻辑另在纯函数用例里测）。"""
    from app.ai_auto.signal_preview import PreviewResult
    from app.ai_auto.signals import StrategySignalView
    from app.engine.simulator import RiskParams
    side = "open_long" if net_to == "long" else "open_short"
    view = StrategySignalView(strategy_id=7, name="EMA 交叉短线", stance="flat",
                              latest_signal=side, strength=0.5, reason="r",
                              risk_params=RiskParams())
    return PreviewResult(board=[view], row_key=(1, 2.0, 3.0, 4.0), bar_ts=_START_TS)


def _flat_board():
    from app.ai_auto.signals import StrategySignalView
    from app.engine.simulator import RiskParams
    return [StrategySignalView(strategy_id=7, name="EMA 交叉短线", stance="flat",
                               latest_signal="none", strength=0.0, reason="",
                               risk_params=RiskParams())]


def _flip_runtime(**settings_kwargs) -> WakeRuntime:
    """一个「已跑过一轮确认根决策」的运行时——`signal_flip` 只有在无新根时才拿得到标签位。"""
    settings = Settings(ai_trade_signal_wake_enabled=True, ai_trade_signal_wake_hold_s=0.0,
                        ai_trade_signal_wake_min_samples=2, **settings_kwargs)
    runtime = WakeRuntime(_BAR, 180.0, settings)
    runtime.note_round_start({"kind": "candle_close"}, px=30000.0, bar_index=3,
                             as_of_ts=_START_TS, now_wall=900.0)
    return runtime


def _observe(runtime: WakeRuntime, now_wall: float, close: float) -> dict | None:
    """喂一次「行情确实变过」的预演结果（`row_key` 随 close 变化，才计入有效采样）。"""
    result = _flip_result()
    object.__setattr__(result, "row_key", (1, close, 3.0, 4.0))
    return runtime.plan_signal_flip(result, _flat_board(), _steady_readings(), now_wall=now_wall)


def test_signal_flip_wake_is_produced_only_after_debounce_confirms():
    runtime = _flip_runtime()
    assert _observe(runtime, 1000.0, 2.0) is None                  # samples=1 < 2
    wake = _observe(runtime, 1015.0, 9.0)
    assert wake is not None and wake["kind"] == "signal_flip"
    assert wake["flip"]["provisional"] is True
    assert wake["flip"]["netStanceTo"] == "long"
    assert wake["flip"]["samples"] == 2 and wake["flip"]["barTs"] == _START_TS
    assert runtime.signal_flip_wakes == 1


def test_a_real_new_bar_outranks_the_flip_label_but_keeps_the_evidence():
    """有新根时 `bar_advanced` 抢标签位，但 `flip` 证据仍随 wire 下发、并进 `alsoTrue`。"""
    runtime = WakeRuntime(_BAR, 180.0, Settings(ai_trade_signal_wake_enabled=True,
                                                ai_trade_signal_wake_hold_s=0.0,
                                                ai_trade_signal_wake_min_samples=2))
    _observe(runtime, 1000.0, 2.0)
    wake = _observe(runtime, 1015.0, 9.0)                          # 尚无决策轮 ⇒ 有新根
    assert wake["kind"] == "bar_advanced"
    assert "signal_flip" in wake["alsoTrue"]
    assert wake["flip"]["netStanceTo"] == "long"


def test_min_gap_suppresses_a_second_flip_wake_without_latching_it():
    """T-15：距上轮不足 `min_gap_s` ⇒ 不唤醒**且不锁存**（翻转仍在跟踪，过了间隔仍能唤醒）。"""
    runtime = _flip_runtime(ai_trade_signal_wake_min_gap_s=120.0)
    _observe(runtime, 1000.0, 2.0)
    assert _observe(runtime, 1015.0, 9.0) is not None              # 首次唤醒（无上一次可比）
    runtime.note_new_bar()                                         # 新根解锁，同方向可再跟踪
    assert _observe(runtime, 1030.0, 11.0) is None                 # samples=1
    assert _observe(runtime, 1045.0, 12.0) is None                 # confirmed 但距上轮仅 30s
    assert _observe(runtime, 1140.0, 13.0) is not None             # 过了 min_gap ⇒ 仍能唤醒
    assert runtime.signal_flip_wakes == 2


def test_signal_flip_switch_off_is_a_no_op():
    runtime = WakeRuntime(_BAR, 180.0, Settings(ai_trade_signal_wake_enabled=False))
    assert runtime.plan_signal_flip(_flip_result(), _flat_board(), _steady_readings(),
                                    now_wall=1000.0) is None
    assert runtime.signal_flip_wakes == 0


async def test_signal_flip_round_still_decides_on_the_confirmed_board(monkeypatch):
    """T-14（轴 C 全部安全性的支点）：翻转唤醒起的决策轮，`signalBoard` **等于确认根信号板**。

    预演结果只出现在 `payload["wake"]["flip"]`；共识门禁、铁律层、edge 门禁、仓位层因此全部仍以
    确认根为准，最坏后果只是多烧一次 token。
    """
    await init_db()
    session = _make_session(wake_interval_s=120.0,
                            settings=Settings(ai_trade_signal_wake_enabled=True,
                                              ai_trade_signal_wake_hold_s=0.0,
                                              ai_trade_signal_wake_min_samples=2))
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        confirmed = session._last_board
        payload = session._build_decision_payload(
            2, _START_TS, 30000.0, confirmed,
            session._wake.classify(session._wake_readings("interval", 30000.0),
                                   now_wall=time.time(),
                                   flip=_flip_result().board and {"netStanceFrom": "flat",
                                                                  "netStanceTo": "long",
                                                                  "strategies": [],
                                                                  "provisional": True}))
        assert payload["wake"]["kind"] == "signal_flip"
        assert payload["wake"]["flip"]["provisional"] is True
        assert [s["strategyId"] for s in payload["signalBoard"]] == \
               [v.strategy_id for v in confirmed]
    finally:
        await dispose_engine()


async def test_unconfirmed_candles_are_recorded_as_the_preview_row_and_cleared_on_close():
    """会话接线：未确认根记 `_live_row`（不入窗），确认根落地即清空并解锁防抖锁存。"""
    await init_db()
    session = _make_session()
    try:
        live = Candle(ts=_START_TS + _STEP, o=30000.0, h=30100.0, l=29900.0, c=30050.0,
                      vol=12.0, confirm=False)
        await session.feed_candle(live)
        assert session._live_row == {"ts": live.ts, "o": 30000.0, "h": 30100.0,
                                     "l": 29900.0, "c": 30050.0, "vol": 12.0}
        assert len(session._window.candles) == 0          # 未确认根**绝不入窗**
        await session.feed_candle(_candle(1))
        await _settle(session)
        assert session._live_row is None
    finally:
        await dispose_engine()


# ============================ A-2 / A-3：轴 B 省轮数、轴 C 抗 repaint ============================
def test_a2_armed_position_watch_cuts_round_count_by_more_than_half():
    """A-2：持仓 + armed + 价格静止 2 小时（唤醒周期 120s ⇒ 基线 60 轮）。

    断言三条：轮数 ≤24（下降 ≥60%）；任意两次真实轮之间 ≤ 4×唤醒周期（上限 3 的直接推论）；
    把 `guardStatus` 换成 `idle` / `unavailable` 重跑，轮数与基线**逐次相等**（这两态一次都不许跳）。
    """
    def _run(*, enabled: bool, status: str) -> tuple[int, int]:
        runtime = _runtime(ai_trade_wake_skip_position_watch=enabled)
        runtime.note_guard_status(status)
        runtime.note_round_start({"kind": "candle_close"}, px=30000.0, bar_index=3,
                                 as_of_ts=_START_TS, now_wall=0.0)
        runtime.note_guard_status(status)
        rounds, gap, worst_gap = 0, 0, 0
        for period in range(60):                          # 2 小时 / 120 秒
            wake = runtime.classify(_position_readings(), now_wall=120.0 * (period + 1))
            assert wake["kind"] == "position_watch"
            if runtime.should_skip(wake):
                runtime.note_skip(True)
                gap += 1
                continue
            rounds += 1
            worst_gap = max(worst_gap, gap + 1)
            gap = 0
            runtime.note_round_start(wake, px=30000.0, bar_index=3, as_of_ts=_START_TS,
                                     now_wall=120.0 * (period + 1))
            runtime.note_guard_status(status)
        return rounds, worst_gap

    baseline, _ = _run(enabled=False, status="armed")
    saved, worst_gap = _run(enabled=True, status="armed")
    assert baseline == 60
    assert saved <= 24, f"轴 B 未达到 ≥60% 的降幅（{saved}/60）"
    assert worst_gap <= 4                                 # 上限 3 ⇒ 最多连跳 3 轮后必放行
    for unarmed in ("idle", "unavailable"):
        assert _run(enabled=True, status=unarmed)[0] == baseline, unarmed


def test_a3_repaint_storm_produces_zero_wakes_and_a_steady_flip_produces_exactly_one():
    """A-3：每 5 秒在 flat↔long 之间抖动的序列 ⇒ **零唤醒**；持续 90 秒的单向翻转 ⇒ 恰好 **1** 次。

    没有防抖的信号驱动唤醒比定时唤醒更糟——它专挑最抖的时候把 AI 叫起来。
    """
    storm = WakeRuntime(_BAR, 180.0, Settings(ai_trade_signal_wake_enabled=True))
    storm.note_round_start({"kind": "candle_close"}, px=30000.0, bar_index=3,
                           as_of_ts=_START_TS, now_wall=0.0)
    for step in range(24):                                # 2 分钟，每 5 秒抖一次
        result = _flip_result("long" if step % 2 else "short")
        object.__setattr__(result, "row_key", (1, 100.0 + step, 3.0, 4.0))
        assert storm.plan_signal_flip(result, _flat_board(), _steady_readings(),
                                      now_wall=5.0 * step) is None
    assert storm.signal_flip_wakes == 0

    steady = WakeRuntime(_BAR, 180.0, Settings(ai_trade_signal_wake_enabled=True))
    steady.note_round_start({"kind": "candle_close"}, px=30000.0, bar_index=3,
                            as_of_ts=_START_TS, now_wall=0.0)
    wakes = 0
    for step in range(19):                                # 90 秒，每 5 秒采样一次，方向不变
        result = _flip_result("long")
        object.__setattr__(result, "row_key", (1, 100.0 + step, 3.0, 4.0))
        if steady.plan_signal_flip(result, _flat_board(), _steady_readings(),
                                   now_wall=5.0 * step) is not None:
            wakes += 1
    assert wakes == 1, "同一次翻转必须被锁存，绝不反复开轮"


# ============================ A-4（硬性）：四轴全开的决策轮数 ≤ 全关基线 ============================
class _AlternatingStrategy(BaseStrategy):
    """每 3 根发一条边沿信号的确定性策略。

    **夹具关键约束（勿优化掉）**：25 根序列里至少有 8 根 K 线在收线时留下**排队单**——那正是
    P0-1 会误触发的形态。若轴 C 的判据退回裸 `stance`，那 8 根会各多出一轮，A-4 必红。
    """

    meta = StrategyMeta(key="_alt_wake_sched", name="Alt", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        from app.strategies.base import Signal
        if i % 3 != 0:
            return []
        side = "close" if ctx.position_side != "flat" else "long"
        return [Signal(side=side, strength=0.5, reason="alt")]


class _FakeClock:
    """可控墙钟：合成序列必须可复现，真实时钟会让轮数对拍变成掷骰子。"""

    def __init__(self, start: float):
        self.now = start

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def _count_rounds_over_synthetic_series(monkeypatch, **switches) -> list[dict]:
    """同一条 15m / 2 分钟唤醒的合成序列跑一遍，返回**每一轮的 wake**（spy 收集，不看耗时）。"""
    from app.ai_auto import session as session_mod
    settings = Settings(**switches, ai_trade_signal_wake_hold_s=15.0,
                        ai_trade_signal_wake_min_samples=2,
                        ai_trade_signal_wake_preview_interval_s=15.0,
                        ai_trade_signal_wake_stale_s=3600.0)
    session = _make_session(wake_interval_s=120.0, settings=settings)
    spec = PaperStrategySpec(1, "Alt", "atomic", _AlternatingStrategy({}), {})
    session._specs = [spec]
    session._window._specs = [spec]
    clock = _FakeClock(1_785_600_000.0)
    monkeypatch.setattr(session_mod, "time", clock)
    rounds: list[dict] = []

    async def _fake_round(board, i, ref_px, ts, wake):
        """忠实复现调度契约（断面冻结 + finally 放开 + 补跑），但不烧 LLM。"""
        rounds.append(wake)
        try:
            session._wake.note_round_start(wake, px=ref_px, bar_index=i, as_of_ts=ts,
                                           now_wall=clock.time())
        finally:
            session._deciding = False
            session._maybe_catchup_wake()

    session._run_decision_round = _fake_round
    # 持仓 + 保护层 armed：这正是轴 B 该省钱的形态（退出已被 5 秒确定性层接管），也是让基线里
    # 真的出现定时轮的前提——空仓静止序列的定时轮会被既有心跳守卫吃掉，两侧都为 0，对拍无意义。
    session.executor.projected = "long"
    queued_bars = 0
    for idx in range(25):        # 25 根 ⇒ 恰好 8 根（i=3,6,…,24）在收线时留下排队单
        await session.feed_candle(_candle(idx, price=30000.0 + idx * 40.0))
        await _settle(session)
        queued_bars += 1 if session._shadows[0]._pending else 0
        for tick in range(4):                             # 一根 15m 内的四个 tick（各 +30s）
            clock.advance(30.0)
            session._last_px_wall = clock.time()
            session._wake.note_guard_status("armed")      # 唯一入口，合成一个「在岗」的保护层
            session._live_row = {"ts": _candle(idx).ts + _STEP, "o": 30000.0 + idx * 40.0,
                                 "h": 30050.0 + idx * 40.0 + tick, "l": 29950.0 + idx * 40.0,
                                 "c": 30000.0 + idx * 40.0 + tick, "vol": 100.0}
            await session._maybe_signal_flip_wake()
            await session._maybe_interval_wake()
            await _settle(session)
    assert queued_bars >= 8, f"夹具失效：只有 {queued_bars} 根带排队单（要求 ≥8）"
    return rounds


async def test_a4_four_axes_on_never_costs_more_llm_rounds_than_the_all_off_baseline(monkeypatch):
    """A-4（硬性）：四轴全开的总决策轮数 **≤** 四轴全关基线。

    本轮的立项理由之一就是省钱，新增一个唤醒源不得把它吃回去。这条同时是 P0-1 的回归护栏。
    """
    await init_db()
    try:
        baseline = await _count_rounds_over_synthetic_series(
            monkeypatch, ai_trade_wake_backlog_enabled=False,
            ai_trade_wake_skip_position_watch=False, ai_trade_signal_wake_enabled=False)
        all_on = await _count_rounds_over_synthetic_series(
            monkeypatch, ai_trade_wake_backlog_enabled=True,
            ai_trade_wake_skip_position_watch=True, ai_trade_signal_wake_enabled=True)
        assert len(all_on) <= len(baseline), f"四轴全开多烧了 LLM：{len(all_on)} > {len(baseline)}"
        # 两个数钉进用例（并写进 spec §九，C-6）：改动调度层后若比值明显劣化，说明省钱的那一半
        # 被吃掉了——只断言「≤」会让「50 → 50」这种「一分没省」的退化悄悄通过。
        assert (len(baseline), len(all_on)) == (50, 34), (
            f"轮数对拍漂移：基线 {len(baseline)} / 全开 {len(all_on)}")
        # 构成也要对得上，否则「省了 16 轮」可能来自某个意外的短路而不是轴 B。
        # 首个 armed tick 是一次真实的「浮盈跨档」（idle→armed），故基线里 24 轮 position_watch + 1 轮
        # profit_tier；后者两侧都不许跳（跨档是新信息），出现在两侧才说明轴 B 没有误伤它。
        assert _kind_counts(baseline) == {"candle_close": 25, "position_watch": 24,
                                          "profit_tier": 1}
    finally:
        await dispose_engine()


def _kind_counts(wakes: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for wake in wakes:
        counts[wake["kind"]] = counts.get(wake["kind"], 0) + 1
    return counts


# ============================ G-2：总闸全开但无事发生 ⇒ 新键整键缺席 ============================
async def test_g2_all_switches_on_but_nothing_happened_keeps_both_new_keys_absent():
    """四轴全开、无丢弃、无翻转、空仓 ⇒ `wake.backlog` / `wake.flip` **缺席**（不是 None）。"""
    await init_db()
    session = _make_session(settings=Settings(
        ai_trade_wake_backlog_enabled=True, ai_trade_wake_skip_position_watch=True,
        ai_trade_signal_wake_enabled=True))
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        _elapse_timer(session)
        await session._maybe_interval_wake()
        await _settle(session)
        wake = session._last_decision["wake"]
        assert "backlog" not in wake and "flip" not in wake
    finally:
        await dispose_engine()


# ============================ G-6 / G-7：预演不碰真身、不拖停盯市 ============================
def _real_state(session) -> list:
    """真身的可比状态断面（策略实例内部字典 + 影子 broker 的全部 stance 相关字段与账本长度）。"""
    import copy as copy_mod
    return [(copy_mod.deepcopy(vars(spec.strategy)), copy_mod.deepcopy(shadow.pos),
             copy_mod.deepcopy(shadow._pending), copy_mod.deepcopy(shadow._risk),
             shadow.cash, shadow.fees_paid, shadow._cur_bar,
             len(shadow.trades), len(shadow.equity_points))
            for spec, shadow in zip(session._specs, session._shadows)]


async def test_g6_a_full_monitor_tick_with_preview_leaves_the_real_objects_untouched():
    """G-6：一整轮盯市 + 预演之后，真身策略与影子 broker 逐字段与预演前相同。"""
    await init_db()
    session = _make_session(settings=Settings(ai_trade_signal_wake_enabled=True,
                                              ai_trade_signal_wake_stale_s=3600.0))
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        await session.feed_candle(Candle(ts=_START_TS + _STEP, o=30000.0, h=30100.0,
                                         l=29900.0, c=30050.0, vol=99.0, confirm=False))
        before = _real_state(session)
        session._preview._next_due_wall = time.time() - 1.0    # 让本 tick 一定到期
        await session._monitor_tick()
        await _settle(session)        # 盯市 tick 可能起真实决策轮；不收干净会留一个悬空 task
        assert session._preview.stats()["previewErrors"] == 0   # 真跑过，不是异常短路
        assert _real_state(session) == before
    finally:
        await dispose_engine()


async def test_g7_a_cpu_bound_preview_never_stalls_the_profit_guard_on_the_same_tick(monkeypatch):
    """G-7：预演假件是 **GIL 密集忙循环**（`sleep` 会释放 GIL，用它测不到真问题）。

    断言盯市 tick 在 ≤1s 内返回，且 `_maybe_profit_exit` 在**同一个 tick 内确实被调用过**
    （用 spy 计数，不看耗时）。

    能力边界如实记录：`asyncio.to_thread` 对 GIL 密集负载**不提供真并行**，它只保证事件循环能按
    `sys.getswitchinterval()` 粒度交错执行，从而让 `wait_for` 的超时按时触发、盯市不被无限期挂住。
    它**不**保证盯市 tick 的延迟不受影响——这正是本轴还需要节流、并发闸、退避与首发默认关闭四道
    减压的原因。
    """
    from app.ai_auto import signal_preview
    await init_db()
    session = _make_session(settings=Settings(ai_trade_signal_wake_enabled=True,
                                              ai_trade_signal_wake_timeout_s=0.2,
                                              ai_trade_signal_wake_stale_s=3600.0))
    try:
        def _gil_hog(specs, shadows, snapshot):
            end = time.monotonic() + 2.0
            spins = 0
            while time.monotonic() < end:
                spins += 1
            return []

        monkeypatch.setattr(signal_preview, "run_preview", _gil_hog)
        exits: list[int] = []
        original_exit = session._maybe_profit_exit

        async def _spy_exit(ts):
            exits.append(ts)
            await original_exit(ts)

        monkeypatch.setattr(session, "_maybe_profit_exit", _spy_exit)
        await session.feed_candle(_candle(0))
        await _settle(session)
        await session.feed_candle(Candle(ts=_START_TS + _STEP, o=30000.0, h=30100.0,
                                         l=29900.0, c=30050.0, vol=99.0, confirm=False))
        session._preview._next_due_wall = time.time() - 1.0     # 让本 tick 一定到期
        started = time.monotonic()
        await session._monitor_tick()
        elapsed = time.monotonic() - started
        await _settle(session)        # 同上：收干净盯市 tick 可能起的决策轮
        assert exits, "保护层必须在同一个 tick 内跑过——预演绝不能把它挤停"
        assert elapsed <= 1.0, f"盯市 tick 被预演拖了 {elapsed:.2f}s"
        assert session._preview.stats()["previewTimeouts"] == 1
        while session._preview._inflight:                 # 等忙循环线程真的结束再收尾
            await asyncio.sleep(0.05)
    finally:
        await dispose_engine()


async def test_axis_c_default_off_never_touches_the_preview_machinery():
    """A-7：轴 C 默认关 ⇒ `_live_row` 照常记录，但一次都不构造克隆、不投递线程（零成本空转）。"""
    await init_db()
    session = _make_session()
    try:
        assert session._preview.enabled is False
        live = Candle(ts=_START_TS + _STEP, o=1.0, h=1.0, l=1.0, c=1.0, vol=1.0, confirm=False)
        await session.feed_candle(live)
        assert session._live_row is not None
        assert session._preview_ready() is False
        await session._maybe_signal_flip_wake()
        assert session._decision_task is None
        assert session._status_payload()["signalFlipWakes"] == 0
    finally:
        await dispose_engine()


async def _no_trade_fee(_inst_id):
    """费率取数桩：恒不可用 ⇒ 回落配置常数（与本文件既有断言的费率口径一致）。"""
    return None


async def _no_position_history():
    """持仓历史取数桩：恒空 ⇒ 对账 status="unavailable"，绝不触网。"""
    return []


# ---------- G-17（ai-trade-intel-actionability）：两个新计数的两处出口同值 ----------
def test_intel_alert_counters_appear_in_both_status_and_metrics():
    """`status_counts()` 与 `stats()` **同源**：后者被 `session.py` 整体赋值进
    `metrics_json["aiStats"]["wakeScheduler"]`，故新键天然落库（评审 P1-7）。

    判据落在**后端**：`lib/aiTradeHistoryTypes.ts` 是一处今天就没有任何读点的纯声明位，
    断言它只会得到一条空闸。
    """
    runtime = WakeRuntime(_BAR, 120.0, Settings())
    assert runtime.status_counts()["intelAlertWakes"] == 0
    assert runtime.stats()["intelAlertDrops"] == 0

    class _Handoff:
        def wire(self, *, now):
            return {"itemId": 7}

    readings = WakeReadings(trigger="interval", position_side="flat", has_resting=False,
                            bar_index=3, px=30000.0)
    wake = runtime.plan_intel_alert(_Handoff(), readings, now_wall=time.time())
    runtime.note_intel_alert_drop()
    assert wake["kind"] == "intel_alert" and wake["intel"] == {"itemId": 7}
    assert runtime.stats()["intelAlertWakes"] == 1
    assert runtime.status_counts()["intelAlertWakes"] == runtime.stats()["intelAlertWakes"]
    assert runtime.status_counts()["intelAlertDrops"] == runtime.stats()["intelAlertDrops"] == 1


def test_planning_without_a_handoff_changes_nothing():
    """`handoff is None` ⇒ 不计数、不产 wake（非告警轮逐字节回退）。"""
    runtime = WakeRuntime(_BAR, 120.0, Settings())
    readings = WakeReadings(trigger="interval", position_side="flat", has_resting=False,
                            bar_index=3, px=30000.0)
    assert runtime.plan_intel_alert(None, readings, now_wall=time.time()) is None
    assert runtime.stats()["intelAlertWakes"] == 0
