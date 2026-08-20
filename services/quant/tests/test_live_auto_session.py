"""LiveAutoSession 单测（live-auto-trading §2.5/§2.6）：priming 不下单、首实时确认根触发开仓、
reconcile 校正 projected、会话级止损触发自动平仓 + 停。

行情 / OKX 全程桩驱动（直接注入 feed_candle），不触网；铁律：占位假凭证。
"""
from __future__ import annotations

import asyncio
import contextlib

import anyio
import pytest

from app.config import Settings
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.db import repositories as repo
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.live_auto.session import LiveAutoSession
from app.okx.accounts import AccountCredentials
from app.okx.private_client import OKXPrivateError
from app.okx.types import Candle
from app.strategies.base import BaseStrategy, Signal, StrategyMeta

_START_TS = 1_700_000_000
_STEP = 3600


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_auto", name="Stub", description="t",
                        default_params={}, params_schema={})

    def __init__(self, signals_by_bar=None):
        super().__init__({})
        self._signals_by_bar = signals_by_bar or {}

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return self._signals_by_bar.get(i, [])


def _candle(idx: int, price: float = 30000.0) -> Candle:
    return Candle(ts=_START_TS + idx * _STEP, o=price, h=price + 5, l=price - 5,
                  c=price, vol=100.0, confirm=True)


def _make_session(signals_by_bar=None, *, run_id=1, stop_loss_usd=None,
                  pos_state=None, risk=None):
    """构造带桩 OKX 回调的会话；pos_state 控制 positions_fn 行为。"""
    pos_state = pos_state if pos_state is not None else {"flat": False, "upl": 0.0}
    calls = {"orders": [], "closes": []}

    async def place_order_fn(order):
        calls["orders"].append(order)
        return {"ok": True, "ordId": "x", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        calls["closes"].append(close)
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        executor = session.executor
        if pos_state.get("flat") or executor.projected == "flat":
            return []
        return [{"instId": "BTC-USDT-SWAP", "pos": executor.entry_qty_sz,
                 "upl": pos_state.get("upl", 0.0)}]

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=risk or RiskParams(), ct_val=0.01,
        lot_sz=0.01, min_sz=0.01, settings=Settings(),
        place_order_fn=place_order_fn, close_position_fn=close_position_fn,
        positions_fn=positions_fn)
    session = LiveAutoSession("liveauto_test", run_id, _StubStrategy(signals_by_bar),
                              "BTC-USDT-SWAP", "1H", executor, stop_loss_usd=stop_loss_usd,
                              priming=False)
    return session, calls, pos_state


def _drain(q: asyncio.Queue) -> list[dict]:
    out: list[dict] = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


async def test_priming_window_does_not_trade(monkeypatch):
    await init_db()
    history = [_candle(i) for i in range(10)]

    class _FakeRest:
        async def get_candles(self, *_a, **_k):
            return history

    monkeypatch.setattr("app.live_auto.session.get_rest_client", lambda: _FakeRest())
    session, calls, _ = _make_session()
    try:
        await session._prime_window()
        assert session._warmed_bars == 10
        assert calls["orders"] == [] and session.executor.trades == []
    finally:
        await dispose_engine()


async def test_first_live_candle_triggers_open():
    await init_db()
    session, calls, _ = _make_session({1: [Signal(side="long")]})
    try:
        await session.feed_candle(_candle(0))   # 暖机根，无信号
        await session.feed_candle(_candle(1))   # 首信号根 → 开仓
        assert len(calls["orders"]) == 1
        assert session.executor.projected == "long"
    finally:
        await dispose_engine()


async def test_trade_position_and_snapshot_preserve_position_pct():
    await init_db()
    session, _, _ = _make_session({1: [Signal(side="long", strength=0.4)]})
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await session.feed_candle(_candle(1))

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


async def test_reconcile_corrects_projected_to_flat():
    await init_db()
    session, calls, pos_state = _make_session({1: [Signal(side="long")]})
    try:
        await session.feed_candle(_candle(0))
        await session.feed_candle(_candle(1))
        assert session.executor.projected == "long"
        pos_state["flat"] = True   # 交易所侧已平（OCO 触发 / 外部干预）
        await session.feed_candle(_candle(2))
        assert session.executor.projected == "flat"
    finally:
        await dispose_engine()


async def test_open_pushes_status_with_position_side():
    # A1：开仓使 projected flat→long 时须推一条带 positionSide=long 的全量 status，否则前端持仓恒显「空仓」
    # (docs/diagnoses/live-auto-position-stale-and-redundant-toggle)。修复前订阅队列无此 status → 必失败。
    await init_db()
    session, calls, _ = _make_session({1: [Signal(side="long")]})
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))   # 暖机根，无信号，方向不变不推 status
        await session.feed_candle(_candle(1))   # 首信号根 → 开多
        assert session.executor.projected == "long"
        sides = [e["data"].get("positionSide") for e in _drain(q) if e["type"] == "status"]
        assert "long" in sides
    finally:
        await dispose_engine()


async def test_monitor_tick_pushes_flat_status_on_external_close():
    # A1（盯市循环路径）：reconcile 侦测外部平仓使 projected long→flat 时，亦须推 positionSide=flat 全量 status，
    # 让前端持仓标签从「多」归位为「空仓」。覆盖 _monitor_tick 与 _on_confirmed 之外的第二条改 projected 路径。
    await init_db()
    session, calls, pos_state = _make_session({1: [Signal(side="long")]})
    try:
        await session.feed_candle(_candle(0))
        await session.feed_candle(_candle(1))
        assert session.executor.projected == "long"
        q = session.subscribe()                  # 订阅在开仓后 → 只看盯市轮事件
        pos_state["flat"] = True                  # 交易所侧已平（OCO 触发 / 外部干预）
        await session._monitor_tick()
        assert session.executor.projected == "flat"
        sides = [e["data"].get("positionSide") for e in _drain(q) if e["type"] == "status"]
        assert "flat" in sides
    finally:
        await dispose_engine()


async def test_session_stop_loss_triggers_auto_stop():
    await init_db()
    sm = get_sessionmaker()
    async with sm() as s:
        run = await repo.create_run(s, strategy_id=1, inst_id="BTC-USDT-SWAP", bar="1H",
                                    mode="live-auto", config={"mode": "live-auto"})
        run_id = run.id
    session, calls, pos_state = _make_session({1: [Signal(side="long")]}, run_id=run_id,
                                              stop_loss_usd=50.0)
    pos_state["upl"] = -100.0   # 浮亏 100 > 止损 50
    try:
        await session.feed_candle(_candle(0))
        await session.feed_candle(_candle(1))
        assert session.state == "stopped"
        assert session.stop_reason == "session_stop_loss"
        assert len(calls["closes"]) >= 1   # 自动平仓
    finally:
        await dispose_engine()


async def test_monitor_loop_triggers_stop_loss_between_bars_and_completes_flatten():
    # H1 + R-2(P0)：盯市循环（非收线）触发会话止损；循环内停止不自取消、flatten 完整执行 + Run 落终态。
    await init_db()
    sm = get_sessionmaker()
    async with sm() as s:
        run = await repo.create_run(s, strategy_id=1, inst_id="BTC-USDT-SWAP", bar="1H",
                                    mode="live-auto", config={"mode": "live-auto"})
        run_id = run.id
    session, calls, pos_state = _make_session({1: [Signal(side="long")]}, run_id=run_id,
                                              stop_loss_usd=50.0)
    try:
        await session.feed_candle(_candle(0))
        await session.feed_candle(_candle(1))   # 开多，此时浮亏为 0，收线不触发
        assert session.executor.projected == "long" and session.state == "running"
        pos_state["upl"] = -100.0               # 两根之间浮亏 100 > 止损 50
        # 模拟「盯市任务即当前任务」：若 stop 不带 current_task 守卫，将取消自身中断 flatten（R-2）。
        session._monitor_task = asyncio.current_task()
        await session._monitor_tick()
        assert session.state == "stopped"
        assert session.stop_reason == "session_stop_loss"
        assert len(calls["closes"]) >= 1                       # 真实平仓被执行
        assert session.flatten_result and session.flatten_result.get("ok") is True
        assert session.flatten_failed is False
        async with sm() as s:
            run = await repo.get_run(s, run_id)
            assert run.status == "stopped"                     # _finalize_run 落终态未被跳过
    finally:
        session._monitor_task = None
        await dispose_engine()


async def test_monitor_loop_emits_degraded_status_on_stale_feed():
    # H3：行情墙钟超阈 → 推 degraded status；flatten_on_stale=False 默认不平仓。
    await init_db()
    session, calls, pos_state = _make_session({1: [Signal(side="long")]})
    try:
        await session.feed_candle(_candle(0))
        await session.feed_candle(_candle(1))
        assert session.executor.projected == "long"
        q = session.subscribe()
        session._last_candle_wall = 1.0          # 远古墙钟 → 必然超阈
        await session._monitor_tick()
        assert session._stale_degraded is True
        assert session.state == "running" and len(calls["closes"]) == 0
        drained = _drain(q)
        assert any(e["type"] == "status" and e["data"].get("degraded") for e in drained)
    finally:
        await dispose_engine()


async def test_stop_flatten_failure_sets_flatten_failed_and_keeps_residual(monkeypatch):
    # H4：停止平仓持续失败 → flattenFailed=True + residual 保留 + 推 LIVE_AUTO_FLATTEN_FAILED，不假报干净。
    monkeypatch.setattr("app.live_auto.executor.asyncio.sleep", _no_sleep)
    await init_db()
    sm = get_sessionmaker()
    async with sm() as s:
        run = await repo.create_run(s, strategy_id=1, inst_id="BTC-USDT-SWAP", bar="1H",
                                    mode="live-auto", config={"mode": "live-auto"})
        run_id = run.id
    session, calls, pos_state = _make_session({1: [Signal(side="long")]}, run_id=run_id)

    async def failing_close(close):
        calls["closes"].append(close)
        raise OKXPrivateError("51008", "余额不足无法平仓")

    session.executor._close_position = failing_close
    try:
        await session.feed_candle(_candle(0))
        await session.feed_candle(_candle(1))
        assert session.executor.projected == "long"
        q = session.subscribe()
        result = await session.stop("user_stop")
        assert result.get("ok") is False
        assert session.flatten_failed is True
        assert session.residual == {"side": "long", "size": session.executor.entry_qty_sz}
        assert session.executor.projected == "long"           # 残留仓未被假平
        assert session._status_payload()["flattenFailed"] is True
        drained = _drain(q)
        assert any(e["type"] == "error" and e["data"]["code"] == "LIVE_AUTO_FLATTEN_FAILED"
                   for e in drained)
    finally:
        await dispose_engine()


async def _no_sleep(*_args, **_kwargs):
    return None


# ---------------- F1：同形的守护循环（docs/diagnoses/ai-trade-interval-wake-dead-monitor-loop）----


async def test_a_failing_monitor_tick_never_kills_the_loop_nor_the_session_stop_loss():
    """回归护栏：一次非 httpx、非 `OKXError` 的异常不许终结盯市循环。

    本会话的 `_monitor_task` 全仓**只在 `start()` 里创建过一次**、没有任何重建分支——它一旦死亡，
    除非整个会话重启否则永不复活，reconcile / 净值 / 停滞看门狗 / 会话止盈止损随之一起静默停摆。
    故断言不止「循环还活着」，还要断言下一跳的会话止损**真的把仓平掉了**。
    """
    await init_db()
    session, calls, pos_state = _make_session({1: [Signal(side="long")]}, stop_loss_usd=50.0)
    session.executor.settings = Settings(live_auto_poll_interval_s=1)
    ticks = {"n": 0}
    original_tick = session._monitor_tick

    async def flaky_tick():
        ticks["n"] += 1
        if ticks["n"] == 1:
            raise anyio.ClosedResourceError()   # 非 httpx、非 OKXError：既有 except 谓词全不匹配
        await original_tick()

    session._monitor_tick = flaky_tick
    task = None
    try:
        await session.feed_candle(_candle(0))
        await session.feed_candle(_candle(1))          # 开多；此刻浮亏为 0，收线不触发
        assert session.executor.projected == "long" and session.state == "running"
        pos_state["upl"] = -100.0                      # 两根之间浮亏 100 > 止损 50

        task = asyncio.create_task(session._monitor_loop())
        session._monitor_task = task
        deadline = 80
        while deadline > 0 and session.state == "running":
            await asyncio.sleep(0.1)
            deadline -= 1

        assert ticks["n"] >= 2, "第一跳失败后循环必须继续跑下一跳"
        assert session.state == "stopped" and session.stop_reason == "session_stop_loss"
        assert len(calls["closes"]) >= 1, "会话止损必须真的平了仓"
    finally:
        if task is not None:
            # 只 cancel 不 await 会让循环在事件循环关闭之后还动 DB（心跳写），症状是别的模块里
            # 冒出 `Event loop is closed`。
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        session._monitor_task = None
        await dispose_engine()


async def test_a_persistently_failing_tick_is_reported_instead_of_failing_silently():
    """连续失败达阈必须推一条降级（error + status **成对**），且不每 5 秒刷屏；恢复后清零并出声。

    只推 `error` 等于没推：分组成员流（`useLiveAutoGroupStream.routeMemberEvent`）**显式丢弃**
    `error` 帧、只消费 `status`（这也是 `executor._on_reconcile_failure` 两条一起推的原因）。
    两条 status 载荷都必须是**全量** `_status_payload()`：前端成员 status 是整体替换，缺键会把
    该成员的 `sessionPnlUsd` / `positionSide` 一并抹掉，分组合计盈亏当场少算这一笔。
    """
    await init_db()
    session, _calls, _pos = _make_session()
    session.executor.settings = Settings(live_auto_reconcile_max_failures=2)
    try:
        q = session.subscribe()
        await session._note_tick_failure(anyio.ClosedResourceError())
        assert not _drain(q), "未达阈不许出声"

        await session._note_tick_failure(anyio.ClosedResourceError())
        events = _drain(q)
        errors = [e for e in events if e["type"] == "error"]
        assert len(errors) == 1
        assert errors[0]["data"]["code"] == "LIVE_AUTO_MONITOR_DEGRADED"
        assert session._degraded_seen is True
        degraded = [e for e in events if e["type"] == "status"]
        assert len(degraded) == 1, "缺了 status 文案，用户那边与「静默失败」逐字等价"
        assert degraded[0]["data"]["message"] == errors[0]["data"]["message"]
        assert degraded[0]["data"]["degraded"] is True
        assert degraded[0]["data"]["positionSide"] == session.executor.projected
        assert "sessionPnlUsd" in degraded[0]["data"], "窄载荷会把成员盈亏抹成缺席"

        await session._note_tick_failure(anyio.ClosedResourceError())
        assert not _drain(q), "已降级不再重复推送"

        await session._note_tick_recovered()
        assert session._tick_fail_streak == 0
        recovered = [e for e in _drain(q) if e["type"] == "status"]
        assert len(recovered) == 1, "恢复也必须出声"
        assert recovered[0]["data"]["degraded"] is False
        assert "sessionPnlUsd" in recovered[0]["data"], "同上：恢复帧也不许是窄载荷"
    finally:
        await dispose_engine()
