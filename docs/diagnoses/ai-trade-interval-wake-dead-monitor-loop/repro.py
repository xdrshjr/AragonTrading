"""最小复现：一次非 OKXError 异常打死 AI 自动交易的盯市循环，墙钟定时唤醒随之永久失效。

复现的是生产日志里真实发生过的那一条（`%APPDATA%/aragon-desktop/logs/backend.log`，
2026-08-19 02:38:48，4 个 `AiTradeSession._monitor_loop` 同时以 `anyio.ClosedResourceError`
终结）：私有 REST 在代理链路抖动 / 笔记本休眠唤醒后抛出一个 **不是 httpx 异常** 的传输层错误，
它穿过 `private_client._get`（只 `except httpx.TransportError`）、`trading.fetch_live_positions`
（只 `except OKXError`）、`executor.reconcile`（只 `except OKXError`）、`session._monitor_loop`
（只 `except asyncio.CancelledError`）四层，把盯市任务打死。

盯市任务是墙钟定时唤醒（`_maybe_interval_wake`）的**唯一**执行者，而确认 K 线唤醒挂在另一条
WS 回调链上——于是用户看到的正是「信号（K 线）还能触发 Agent，定时触发再也不来了」。

运行（离线，不需要网络 / 不需要真实凭证）：

    cd services/quant
    python -u ../../docs/diagnoses/ai-trade-interval-wake-dead-monitor-loop/repro.py

退出码 0 = 缺陷已复现（盯市任务死亡 + 定时唤醒停摆 + K 线唤醒仍在）；退出码 1 = 未复现。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "services" / "quant"))
os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(),
                                              f"aragon_repro_{uuid.uuid4().hex}.db"))
os.environ.setdefault("ALLOW_CACHED_FALLBACK", "false")

import anyio  # noqa: E402

from app.agent.llm.base import StreamEvent  # noqa: E402
from app.ai_auto.decider import AiTradeDecider  # noqa: E402
from app.ai_auto.session import AiTradeSession  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db.session import dispose_engine, init_db  # noqa: E402
from app.engine.simulator import RiskParams  # noqa: E402
from app.live_auto.executor import LiveExecutor  # noqa: E402
from app.okx.accounts import AccountCredentials  # noqa: E402
from app.okx.types import Candle  # noqa: E402
from app.paper.strategy_factory import PaperStrategySpec  # noqa: E402
from app.strategies.base import BaseStrategy, StrategyMeta  # noqa: E402

_START_TS = 1_700_000_000
_STEP = 3600
_WAKE_INTERVAL_S = 120.0
_POLL_S = 1                     # 盯市周期压到 1s，整个复现约 10 秒


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_repro_wake", name="Stub", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


class _StubLLM:
    """决策流返回 hold；摘要流返回一句文本。全程不出网。"""

    async def stream(self, *, system, messages, tools):
        if tools:
            args = {"action": "hold", "reason": "r", "confidence": 0.7}
            yield StreamEvent("tool_use_start", id="t", name="submit_trade_decision")
            yield StreamEvent("tool_use_args_delta", id="t",
                              partial_json=json.dumps(args, ensure_ascii=False))
            yield StreamEvent("message_done", stop_reason="tool_use")
        else:
            yield StreamEvent("text_delta", text="summary")
            yield StreamEvent("message_done", stop_reason="end")


def _candle(idx: int, price: float = 30000.0) -> Candle:
    return Candle(ts=_START_TS + idx * _STEP, o=price, h=price + 5, l=price - 5,
                  c=price, vol=100.0, confirm=True)


class _Fault:
    """持仓读数的故障注入开关：置位后抛一次 anyio.ClosedResourceError（生产里的那一类）。"""

    armed = False
    fired = False


def _make_session() -> AiTradeSession:
    settings = Settings(ai_trade_wake_skip_unchanged_pct=0.0,     # 关掉空仓去重守卫，只看唤醒本身
                        live_auto_poll_interval_s=_POLL_S)

    async def place_order_fn(order):
        return {"ok": True, "ordId": "x", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        if _Fault.armed and not _Fault.fired:
            _Fault.fired = True
            # 生产原样：代理 CONNECT 隧道在 TLS 握手中途被拆 → anyio 层异常，**不是** httpx 异常。
            raise anyio.ClosedResourceError()
        return []

    async def fills_fn():
        return []

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(), ct_val=0.01,
        lot_sz=0.01, min_sz=0.01, settings=settings,
        place_order_fn=place_order_fn, close_position_fn=close_position_fn,
        positions_fn=positions_fn, fills_fn=fills_fn)
    decider = AiTradeDecider(_StubLLM(), "sys", context_refresh_rounds=10, settings=settings)
    spec = PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})
    session = AiTradeSession("aitrade_repro", 1, [spec], "BTC-USDT-SWAP", "1H", executor,
                             decider, priming=False, priming_bars=10,
                             context_refresh_rounds=10, wake_interval_s=_WAKE_INTERVAL_S)

    async def no_htf_data(*_args, **_kwargs):
        return []

    session._window._safe_get_candles = no_htf_data          # 高周期 / 战术窗口取数：离线桩
    session._cost.schedule_maintenance = lambda *a, **k: None  # 资金费 / 费率维护：离线桩
    return session


def _elapse_timer(session: AiTradeSession) -> None:
    """把决策计时器回拨到「已到点」，使下一个盯市 tick 判定该起一轮定时唤醒。"""
    session._last_decision_started_wall = time.time() - (_WAKE_INTERVAL_S + 5)


async def _settle(session: AiTradeSession) -> None:
    task = session._decision_task
    if task is not None:
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _wait_rounds(session: AiTradeSession, target: int, timeout_s: float) -> bool:
    """等 rounds_completed 达到 target（含决策任务收尾）。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        await asyncio.sleep(0.2)
        await _settle(session)
        if session.rounds_completed >= target:
            return True
    return False


async def main() -> int:
    await init_db()
    session = _make_session()
    failures: list[str] = []
    try:
        # ---- 0. 一根确认 K 线：装上 _last_board（定时唤醒的前置条件），并跑掉第 1 轮 ----
        await session.feed_candle(_candle(0))
        await _settle(session)
        print(f"[0] candle round done, rounds={session.rounds_completed}")

        session._monitor_task = asyncio.create_task(session._monitor_loop())

        # ---- 1. 基线：盯市循环活着，到点即起一轮 trigger=interval ----
        _elapse_timer(session)
        ok = await _wait_rounds(session, 2, timeout_s=8.0)
        print(f"[1] baseline interval wake fired={ok}, rounds={session.rounds_completed}")
        if not ok:
            failures.append("baseline: interval wake did not fire while monitor loop alive")

        # ---- 2. 注入一次非 httpx / 非 OKXError 的传输层异常 ----
        _Fault.armed = True
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and not session._monitor_task.done():
            await asyncio.sleep(0.2)
        dead = session._monitor_task.done()
        exc = session._monitor_task.exception() if dead else None
        print(f"[2] monitor task dead={dead}, exception={exc!r}")
        if not (dead and isinstance(exc, anyio.ClosedResourceError)):
            failures.append("monitor loop survived the injected error (bug not reproduced)")

        # ---- 3. 症状 A：墙钟定时唤醒再也不来（等 5 个盯市周期）----
        rounds_before = session.rounds_completed
        _elapse_timer(session)
        await asyncio.sleep(_POLL_S * 5)
        await _settle(session)
        print(f"[3] after {_POLL_S * 5}s: rounds={session.rounds_completed} "
              f"(was {rounds_before}) -> interval wake "
              f"{'DEAD' if session.rounds_completed == rounds_before else 'alive'}")
        if session.rounds_completed != rounds_before:
            failures.append("interval wake still fires (bug not reproduced)")

        # ---- 4. 症状 B：确认 K 线唤醒照常工作（用户看到的「只有信号还能触发」）----
        await session.feed_candle(_candle(1, price=30100.0))
        await _settle(session)
        candle_alive = session.rounds_completed == rounds_before + 1
        print(f"[4] candle wake still alive={candle_alive}, rounds={session.rounds_completed}")
        if not candle_alive:
            failures.append("candle wake also stopped (symptom does not match the report)")

        # ---- 5. 面板仍宣称「已武装 / 运行中」：本故障对生命周期真值层完全不可见 ----
        wire = session.lifecycle_wire()
        print(f"[5] lifecycle wire: state={wire.get('state')} armed={wire.get('armed')} "
              f"reason={wire.get('reason')} nextWakeInS={wire.get('nextWakeInS')}")
    finally:
        task = getattr(session, "_monitor_task", None)
        if task is not None and not task.done():
            task.cancel()
        await dispose_engine()

    if failures:
        for line in failures:
            print(f"NOT REPRODUCED: {line}")
        return 1
    print("REPRODUCED: monitor loop died on a non-OKXError, interval wake is gone, "
          "candle wake keeps working")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
