"""持仓周期铁律层的**安全底线**用例（ai-trade-holding-horizon-modes §6.1 · P0 级）。

本层唯一不可动摇的语义是：**它只约束 Agent 主动发起的换手**。止损、交易所 OCO、利润保护层、
会话级止盈止损、executor 的强制时间平仓、`stop()` 的 flatten、外部干预的合成平仓——七条结构性
豁免路径都不经过 `_apply_horizon_gate`，因此天然不受影响。

「天然不受影响」不是口头承诺，必须写成断言：本文件把 `min_hold_s` 拉到 **86400 秒（一整天）**，
再逐条走这些路径，断言仓位**依然能被平掉**。任何一条失败都是阻断级缺陷——它意味着本层把一笔真实
持仓锁死了。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.agent.llm.base import StreamEvent
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.hold_horizon import HorizonRule
from app.ai_auto.profit_guard import ProfitGuardRule
from app.ai_auto.session import AiTradeSession
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
_PX = 30_000.0
# 一整天的最短持仓窗口：任何一条豁免路径若真的被本层挡住，下面的断言必然失败。
_LOCKED = HorizonRule(profile_id="custom", title="锁死", min_hold_s=86400.0,
                      exit_confirm_rounds=5, hysteresis_k=5.0,
                      target_hold_min_s=172800.0, target_hold_max_s=259200.0,
                      confirm_window_s=43200.0, expected_hold_bars=48)


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_horizon_exempt", name="Stub", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


class _StubLLM:
    """恒开多的桩决策器（本文件只需要把仓位开起来，不检验决策质量）。"""

    async def stream(self, *, system, messages, tools):
        if tools:
            args = {"action": "open_long", "reason": "r", "confidence": 0.7,
                    "sizeFraction": 0.5}
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


def _make_session(*, risk=None, profit_rule=None, take_profit_usd=None,
                  positions_empty=False):
    settings = Settings(ai_trade_funding_enabled=False)
    state = {"positionsEmpty": positions_empty}

    async def place_order_fn(order):
        return {"ok": True, "ordId": "x", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        ex = session.executor
        if state["positionsEmpty"] or ex.projected == "flat":
            return []
        return [{"instId": "BTC-USDT-SWAP", "pos": ex.entry_qty_sz, "upl": 0.0}]

    async def fills_fn():
        return []

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=risk or RiskParams(), ct_val=0.01,
        lot_sz=0.01, min_sz=0.01, settings=settings, place_order_fn=place_order_fn,
        close_position_fn=close_position_fn, positions_fn=positions_fn, fills_fn=fills_fn)
    decider = AiTradeDecider(_StubLLM(), "sys", context_refresh_rounds=50, settings=settings)
    spec = PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})
    session = AiTradeSession("aitrade_exempt", 1, [spec], "BTC-USDT-SWAP", "1H", executor,
                             decider, priming=False, priming_bars=10,
                             context_refresh_rounds=50, horizon_rule=_LOCKED,
                             profit_rule=profit_rule, take_profit_usd=take_profit_usd)

    async def no_htf(*_a, **_k):
        return []

    session._window._safe_get_candles = no_htf
    return session, state


async def _settle(session) -> None:
    task = session._decision_task
    if task is not None:
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _open_position(session, idx: int = 0) -> None:
    """跑一轮决策把仓位开起来（开仓**不受本层约束**，这本身也是豁免矩阵的一条）。"""
    await session.feed_candle(_candle(idx))
    await _settle(session)
    assert session.executor.projected == "long", "空仓开仓不该被持仓周期层拦住"


async def test_agent_close_is_actually_blocked_by_the_lock():
    """前提可证伪性：同一把锁**确实**拦得住 Agent 主动平仓——否则下面五条豁免断言毫无意义。"""
    await init_db()
    session, _ = _make_session()
    try:
        await _open_position(session)
        session.decider._llm = _StubLLM()          # 保持桩，改用直接调用门禁验证判定
        from app.ai_auto.decider import TradeDecision

        session._horizon.note_intent("close", None, position_side="long",
                                     now_wall=session.started_ts + 1.0)
        decision, degrade, _ = await session._apply_horizon_gate(
            1, _START_TS, TradeDecision("close", None, 0.7, "exit"), None)
        assert decision.action == "hold"
        assert degrade.startswith("horizon_min_hold(")
    finally:
        await dispose_engine()


async def test_session_stop_loss_take_profit_still_flattens():
    """豁免 ①：会话级 USD 止盈 ⇒ `stop()` ⇒ flatten——本层从不参与这条路径。"""
    await init_db()
    session, _ = _make_session()
    try:
        await _open_position(session)
        # 开仓之后再武装阈值（桩 broker 下会话累计盈亏恒为 0，`pnl >= 0` 即触发）：本用例要验的是
        # **路径**（会话止盈 ⇒ stop ⇒ flatten 不经本层），不是阈值算术；启动时就置 0 会在开仓那一轮
        # 的 `_post_round` 里立刻停会话，反而验不到「锁着的持仓能不能被平掉」。
        session.take_profit_usd = 0.0
        await session._maybe_session_exit(_PX, _START_TS + _STEP)
        assert session.executor.projected == "flat"
        assert session.state == "stopped"
    finally:
        await dispose_engine()


async def test_stop_flatten_still_closes_the_position():
    """豁免 ②：用户点停止 ⇒ `stop()` 的 flatten 一定要能平掉自管仓（安全优先，绝不守门）。"""
    await init_db()
    session, _ = _make_session()
    try:
        await _open_position(session)
        result = await session.stop("user_stop")
        assert result.get("ok") is True
        assert session.executor.projected == "flat"
    finally:
        await dispose_engine()


async def test_executor_max_hold_time_exit_still_fires():
    """豁免 ③：executor 的强制时间平仓（`max_hold_bars`）在 `apply` 内部先于信号执行，不经本层。

    刻意直调 `executor.apply`：`_maybe_time_exit` 位于 `apply` 的**第一行**（`executor.py:203`），
    它是否触发只取决于 `bar_index - entry_bar >= max_hold_bars`，与决策链上的任何门禁无关——这正是
    §2.3.3 `max_hold_bars` 豁免要闭环的那条路径（上限已到、executor 下一跳就会强平，此刻本层整体
    解除，绝不出现「系统要强平、本层却拦着 Agent 先走一步」的对冲态）。
    """
    await init_db()
    session, _ = _make_session(risk=RiskParams(max_hold_bars=1))
    try:
        await _open_position(session, idx=0)
        events = await session.executor.apply([], ref_px=_PX, bar_index=1,
                                              ts=_START_TS + _STEP)
        assert session.executor.projected == "flat"
        assert any(e.get("type") == "trade" and (e.get("data") or {}).get("side") == "close"
                   for e in events)
    finally:
        await dispose_engine()


async def test_external_close_is_reconciled_even_while_locked():
    """豁免 ④：交易所侧仓位被外部平掉 ⇒ reconcile 合成一笔 close，本层不会把它挡在门外。"""
    await init_db()
    session, state = _make_session()
    try:
        await _open_position(session)
        state["positionsEmpty"] = True             # 模拟用户在 OKX App 上手动平仓
        await session._monitor_tick()
        assert session.executor.projected == "flat"
    finally:
        await dispose_engine()


async def test_profit_guard_exit_still_fires_while_locked():
    """豁免 ⑤：利润保护层的确定性平仓不经决策链，锁得再死也照常兑现（本层极性 fail-open 的前提）。"""
    await init_db()
    guard = ProfitGuardRule(enabled=True, arm_return_pct=0.01, giveback_pct=0.4,
                            min_hold_s=0.0, hard_tp_return_pct=0.02)
    session, _ = _make_session(profit_rule=guard)
    try:
        await _open_position(session)
        # 硬止盈：保证金收益 2% ⇒ 3× 杠杆下价格 +0.67% 足够触发。
        session._last_live_px = _PX * 1.01
        session._last_px_wall = session.executor.settings and __import__("time").time()
        await session._maybe_profit_exit(_START_TS + _STEP)
        assert session.executor.projected == "flat"
        assert session.profit_guard_exits == 1
    finally:
        await dispose_engine()


@pytest.mark.parametrize("action", ["hold", "cancel_order", "open_long"])
async def test_non_gated_actions_pass_through_even_when_locked(action):
    """豁免 ⑥：`hold` / `cancel_order` / 空仓开仓 **永不**被拦（豁免矩阵冻结，纯核心已守，此处守接线）。"""
    await init_db()
    session, _ = _make_session()
    try:
        from app.ai_auto.decider import TradeDecision

        decision = TradeDecision(action, 0.5, 0.8, "r")
        out, degrade, _ = await session._apply_horizon_gate(1, _START_TS, decision, None)
        assert out is decision and degrade is None
    finally:
        await dispose_engine()
