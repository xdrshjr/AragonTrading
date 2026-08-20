"""Agent 墙钟定时唤醒单测（ai-auto-trading-wake-interval §6.1）。

覆盖第三触发源「墙钟定时唤醒」的触发 / 复用 / 跳过 / 关闭 / 计时器合流 + 后端夹紧解析：
定时到点用「最近确认根信号板 + 当前实时价」起一轮标准决策（trigger="interval"）；in-flight /
行情陈旧 / 关闭 / 尚无信号板一律不唤醒；任一轮决策开始即刷新计时器（K 线唤醒与定时唤醒合流去重）。

行情 / OKX / LLM 全程桩驱动（直接注入 feed_candle + 直调 _maybe_interval_wake + 回拨墙钟），
铁律：占位假凭证。定时轮以 asyncio.create_task 运行，断言前须 await session._decision_task（RN-5）。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time

import anyio
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
    """wake_interval_s=120：一根确认根决策后回拨计时器 → _maybe_interval_wake 起一轮 interval 决策。

    R-03（hold-inertia-fix）适配：本用例价格恒 30000、无新根——F4c 守卫默认阈值（0.05%）下必命中
    跳过，故显式 `ai_trade_wake_skip_unchanged_pct=0.0` 关闭守卫构造；断言语义 =「守卫关闭时定时
    唤醒行为与升级前逐字相同」（断言本体原样保留，不得删除或放松）。
    """
    await init_db()
    session = _make_session(action="hold", wake_interval_s=120,
                            settings=Settings(ai_trade_wake_skip_unchanged_pct=0.0))
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


# ---------- F4c 空仓重复唤醒跳过守卫（ai-trade-reflection-hold-inertia-fix §六-4 / §六-8）----------
async def test_interval_wake_skips_when_flat_and_unchanged():
    """空仓+无挂单+无新确认根+价格微动（<0.05%）→ 不创建决策任务、wake_skips 恰 +1、计时器未刷新；
    同一唤醒周期窗口内紧接着再调 → 仍跳过且 wake_skips **不再递增**（R-02 计数节流）。"""
    await init_db()
    session = _make_session(action="hold", wake_interval_s=120)   # 默认 Settings → 守卫开启（0.05%）
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        assert session.rounds_completed == 1
        session._last_live_px = 30003.0            # 相对 30000 仅 +0.01% < 0.05% → 微动
        _elapse_timer(session)
        wall_before = session._last_decision_started_wall
        task_before = session._decision_task
        await session._maybe_interval_wake()
        assert session._decision_task is task_before               # 未起新决策任务
        assert session.rounds_completed == 1
        assert session.wake_skips == 1
        assert session._last_decision_started_wall == wall_before  # 计时器未刷新（下个 tick 重评估）
        await session._maybe_interval_wake()       # 同窗口第二次：仍跳过、不再递增（节流）
        assert session.rounds_completed == 1
        assert session.wake_skips == 1
        assert session._status_payload()["wakeSkips"] == 1         # status 加法键随行
    finally:
        await dispose_engine()


async def test_interval_wake_not_skipped_when_holding_position():
    """持仓轮任何阈值下都不跳过（盯市职责不受守卫影响）：开多后价格纹丝不动仍照常唤醒。"""
    await init_db()
    session = _make_session(action="open_long", wake_interval_s=120)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        assert session.executor.projected == "long"
        _elapse_timer(session)
        await session._maybe_interval_wake()
        await _settle(session)
        assert session.rounds_completed == 2       # 照常唤醒
        assert session.wake_skips == 0
    finally:
        await dispose_engine()


async def test_interval_wake_not_skipped_with_resting_order():
    """挂单轮不跳过：空仓但有未成交限价挂单 → Agent 须能盯它并择机撤单。"""
    from app.live_auto.resting import RestingOrder
    await init_db()
    session = _make_session(action="hold", wake_interval_s=120)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        session.executor.resting = RestingOrder(
            cl_ord_id="aragon-1", ord_id="okx-1", intent="open", side="long",
            px=29900.0, sz=0.03, strength=0.5, reason="r",
            created_ts=_START_TS, expire_ts=_START_TS + 900, adopted_sz=0.0)
        _elapse_timer(session)
        await session._maybe_interval_wake()
        await _settle(session)
        assert session.rounds_completed == 2       # 照常唤醒
        assert session.wake_skips == 0
    finally:
        await dispose_engine()


async def test_interval_wake_resumes_when_price_crosses_threshold():
    """价格越阈（≥0.05%）即恢复唤醒——守卫命中不刷新计时器，下个 tick 价格一越阈立即唤醒。"""
    await init_db()
    session = _make_session(action="hold", wake_interval_s=120)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        session._last_live_px = 30003.0            # 微动 → 先被跳过
        _elapse_timer(session)
        await session._maybe_interval_wake()
        assert session.rounds_completed == 1 and session.wake_skips == 1
        session._last_live_px = 30030.0            # +0.1% ≥ 0.05% → 越阈
        await session._maybe_interval_wake()       # 计时器未刷新 → 「到点」仍成立，立即恢复
        await _settle(session)
        assert session.rounds_completed == 2
    finally:
        await dispose_engine()


async def test_interval_wake_resumes_on_new_confirmed_bar():
    """自上一决策轮以来出现新确认根（如 in-flight 跳过轮已推进信号板）→ 守卫放行。"""
    await init_db()
    session = _make_session(action="hold", wake_interval_s=120)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        session._deciding = True                   # 模拟决策在飞：新根被 in-flight 跳过但推进窗口
        await session.feed_candle(_candle(1))
        session._deciding = False
        assert session._last_bar_index == 1 and session._last_round_bar_index == 0
        _elapse_timer(session)
        await session._maybe_interval_wake()       # 价格未动但有新根 → 不跳过
        await _settle(session)
        assert session.rounds_completed == 2
        assert session.wake_skips == 0
    finally:
        await dispose_engine()


async def test_wake_skip_pushes_status_once_per_window():
    """O1（hold-inertia-observability）：守卫命中且计数成功 → 恰推一条 status（含递增后的 wakeSkips）；
    同一唤醒周期窗口内再次命中 → 不再推送、计数不再递增（R-02 节流语义对事件与计数一致）。"""
    await init_db()
    session = _make_session(action="hold", wake_interval_s=120)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        q = session.subscribe()                    # 决策轮后再订阅：队列只看守卫命中期间的事件
        session._last_live_px = 30003.0            # +0.01% < 0.05% → 守卫命中
        _elapse_timer(session)
        await session._maybe_interval_wake()
        statuses = [e for e in _drain(q) if e["type"] == "status"]
        assert len(statuses) == 1                  # 计数成功恰推一条 status
        assert statuses[0]["data"]["wakeSkips"] == 1
        await session._maybe_interval_wake()       # 同窗口第二次：计数被节流 → 不再推送
        assert session.wake_skips == 1
        assert [e for e in _drain(q) if e["type"] == "status"] == []
    finally:
        await dispose_engine()


async def test_interval_wake_guard_disabled_at_zero_threshold():
    """ai_trade_wake_skip_unchanged_pct=0 → 守卫关闭：数据完全未变也照常唤醒（一键回退口）。"""
    await init_db()
    session = _make_session(action="hold", wake_interval_s=120,
                            settings=Settings(ai_trade_wake_skip_unchanged_pct=0.0))
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        _elapse_timer(session)
        await session._maybe_interval_wake()
        await _settle(session)
        assert session.rounds_completed == 2
        assert session.wake_skips == 0
    finally:
        await dispose_engine()


# ---------- 唤醒感知上下文（ai-trade-wake-aware-context §3.2，纯加法：既有断言全保留）----------
async def test_wake_object_is_passed_through_to_the_decision_event():
    """定时轮的 `wake` 对象随 decision 事件下发，且 `data.trigger` 语义与取值逐字不变（G-4）。"""
    await init_db()
    session = _make_session(action="hold", wake_interval_s=120,
                            settings=Settings(ai_trade_wake_skip_unchanged_pct=0.0))
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        _drain(q)
        _elapse_timer(session)
        await session._maybe_interval_wake()
        await _settle(session)
        decision = [e for e in _drain(q) if e["type"] == "decision"][-1]["data"]
        assert decision["trigger"] == "interval"           # 既有断言语义一字不改
        wake = decision["wake"]
        assert wake["trigger"] == "interval"
        assert wake["kind"] in {"candle_close", "bar_advanced", "resting_order", "profit_tier",
                                "price_breakout", "position_watch", "heartbeat"}
        # 阈值随 wire 下发：读者能自行反算门槛，杜绝「静默门禁」
        assert wake["moveThresholdAtrEff"] == pytest.approx(
            wake["moveThresholdAtr"] * wake["horizonScale"])
    finally:
        await dispose_engine()


async def test_reason_switch_off_leaves_the_decision_event_wake_free():
    """总闸关闭 ⇒ `data.wake` 为 None、`data.trigger` 照旧（A-6 回退的活证据）。"""
    await init_db()
    session = _make_session(action="hold", wake_interval_s=120,
                            settings=Settings(ai_trade_wake_reason_enabled=False,
                                              ai_trade_wake_skip_unchanged_pct=0.0))
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        _drain(q)
        _elapse_timer(session)
        await session._maybe_interval_wake()
        await _settle(session)
        decision = [e for e in _drain(q) if e["type"] == "decision"][-1]["data"]
        assert decision["wake"] is None
        assert decision["trigger"] == "interval"
    finally:
        await dispose_engine()


# ---------------- F1：盯市循环是墙钟唤醒的唯一执行者，它不许被一次异常打死 ----------------
# （docs/diagnoses/ai-trade-interval-wake-dead-monitor-loop）


async def _run_monitor_until(session, predicate, *, timeout_s: float = 8.0) -> bool:
    """跑真实 `_monitor_loop`，轮询到 `predicate()` 为真或超时；返回**收尾前**循环是否还活着。

    存活判定必须由本函数交出来：收尾会 cancel 掉这条任务，之后再问 `task.done()` 恒为 True，
    调用方那句断言就成了一句空话。而收尾**只 cancel 不 await 也不行**——盯市 tick 里有 DB 写
    （心跳），一条还没真正停下的循环会在用例结束、事件循环关闭之后继续动它，症状是别的模块里
    冒出 `Event loop is closed`。
    """
    task = asyncio.create_task(session._monitor_loop())
    session._monitor_task = task
    deadline = time.monotonic() + timeout_s
    try:
        while time.monotonic() < deadline and not predicate():
            await asyncio.sleep(0.1)
            await _settle(session)
    finally:
        alive = not task.done()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    return alive


async def test_a_failing_monitor_tick_never_kills_the_loop_nor_the_interval_wake():
    """回归护栏：盯市 tick 抛出一个既不是 httpx 异常、也不是 `OKXError` 的异常时，循环必须**活着**
    跑下一跳，且下一跳的墙钟定时唤醒**仍能起轮**。

    只断言「没抛出」是不够的：修复前的症状恰恰是「循环死了、K 线唤醒还在、面板一切正常」，
    `_maybe_interval_wake` 又是 `_monitor_tick` 的最后一行——所以必须断言唤醒本身还活着。
    """
    await init_db()
    session = _make_session(action="hold", wake_interval_s=120,
                            settings=Settings(ai_trade_wake_skip_unchanged_pct=0.0,
                                              live_auto_poll_interval_s=1))
    ticks = {"n": 0}
    original_tick = session._monitor_tick

    async def flaky_tick():
        ticks["n"] += 1
        if ticks["n"] == 1:
            # 生产原样：代理 CONNECT 隧道在 TLS 握手中途被拆 ⇒ anyio 层异常，**不是** httpx 异常，
            # 也不是 OKXError，四层 except 的谓词全不匹配。
            raise anyio.ClosedResourceError()
        await original_tick()

    session._monitor_tick = flaky_tick
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        assert session.rounds_completed == 1
        _elapse_timer(session)

        alive = await _run_monitor_until(session, lambda: session.rounds_completed >= 2)

        assert alive, "一次 tick 异常绝不许终结盯市循环"
        assert ticks["n"] >= 2, "第一跳失败后循环必须继续跑下一跳"
        assert session.rounds_completed == 2, "墙钟定时唤醒必须仍能起轮"
    finally:
        session.state = "stopped"
        session._monitor_task = None
        await dispose_engine()


async def test_a_persistently_failing_tick_is_reported_instead_of_failing_silently():
    """F1 的组成部分（§8.1 (a)），不是装饰。

    tick 级守卫意味着「一步失败即跳过本 tick 余下全部步骤」，而 reconcile 在第一步、
    `_maybe_interval_wake` 在最后一步：失败若持续存在，循环虽然活着，定时唤醒依然永不执行——
    症状与修复前**完全一样**，只是这次连一条 traceback 都不会有。故达阈必须出声，且只出一次。
    """
    await init_db()
    session = _make_session(action="hold", wake_interval_s=120,
                            settings=Settings(live_auto_reconcile_max_failures=2))
    try:
        q = session.subscribe()
        await session._note_tick_failure(anyio.ClosedResourceError())
        assert not [e for e in _drain(q) if e["type"] == "error"], "未达阈不许刷屏"

        await session._note_tick_failure(anyio.ClosedResourceError())
        errors = [e for e in _drain(q) if e["type"] == "error"]
        assert len(errors) == 1
        assert errors[0]["data"]["code"] == "AI_TRADE_MONITOR_DEGRADED"
        assert session._degraded_seen is True

        await session._note_tick_failure(anyio.ClosedResourceError())
        assert not [e for e in _drain(q) if e["type"] == "error"], "已降级不再重复推送"

        await session._note_tick_recovered()
        assert session._tick_fail_streak == 0
        recovered = [e for e in _drain(q) if e["type"] == "status"]
        assert len(recovered) == 1, "恢复也必须出声"
        # 载荷必须是**全量**断面：status 事件经 `store.patchAiTradeMemberStatus` 整体浅合并进成员
        # 断面，一份缺键的 status 会把 `profitGuardCooldown` 一类只在全量断面里出现的键清成 null。
        assert recovered[0]["data"]["positionSide"] == session.executor.projected
        assert "sessionPnlUsd" in recovered[0]["data"]
    finally:
        await dispose_engine()


async def test_a_throwing_recovery_notice_never_kills_the_loop_either():
    """出声路径本身也必须在守卫**之内**。

    「成功即清零并出声」若写在 `try` 的 `else` 分支里，它抛出的异常**不被那个 try 接住**——守护
    循环照样被打死，缺陷以完全相同的症状复发一次。恢复文案要读整份 `_status_payload()`，那不是
    一句不会抛的字面量，所以这条缝必须堵上。
    """
    await init_db()
    session = _make_session(action="hold", wake_interval_s=120,
                            settings=Settings(live_auto_poll_interval_s=1))
    notices = {"n": 0}

    async def flaky_notice():
        notices["n"] += 1
        if notices["n"] == 1:
            raise RuntimeError("status payload blew up")

    session._note_tick_recovered = flaky_notice
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)

        alive = await _run_monitor_until(session, lambda: notices["n"] >= 2)

        assert alive, "出声失败同样不许终结盯市循环"
        assert notices["n"] >= 2, "第一跳出声失败后循环必须继续跑下一跳"
    finally:
        session.state = "stopped"
        session._monitor_task = None
        await dispose_engine()
