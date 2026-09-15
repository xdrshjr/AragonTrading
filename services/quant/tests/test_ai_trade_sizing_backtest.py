"""仓位 / 刹车层的回测同构测试（vol-sizing-and-drawdown-brake §6.4 / T-19/T-19b/T-20 + 免责文案）。

两个**复发点**各有一条独立回归测试，两条都必须是「照着最自然的写法实现就会红」的那种：

- **T-19（复发点一）**：`_atr_series` 的构造条件若不把总闸②或进来，回测里 `_atr_at` 恒 `None` ⇒ ATR
  止损恒不生效**且不报任何错**（阶段一 I-5 的同款静默）。
- **T-19b（复发点二，[P0-3]）**：止损价是 `SimBroker._open` 时由 `self._risk` **冻结**进 `pos` 的，
  `apply_risk(bar_index, row, _risk)` 的第三形参根本没被使用——只改 `self._oco_risk`（那个正是传给
  `apply_risk` 的对象）**一行都不会生效**。本用例直接断言「不同 ATR 下开的仓，止损价/开仓价比值不同」，
  并另用一个只改 `_oco_risk` 的对照实现证明该判据真的能把错误写法判红。
"""
from __future__ import annotations

import json

import pytest

from sqlalchemy import select

from app.agent.llm.base import StreamEvent
from app.ai_auto.backtest import AiBacktestRunner, AiBacktestSpec, _BacktestJob
from app.ai_auto.decider import TradeDecision
from app.ai_auto.sizing_runtime import build_sizing_runtime
from app.ai_auto.sizing_text import SIZING_BACKTEST_NOTE
from app.config import Settings
from app.db import repositories as repo
from app.db.models import Strategy
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.engine.simulator import RiskParams, SimBroker
from app.paper.strategy_factory import build_paper_strategy_spec
from app.strategies.base import Signal

from tests.conftest import make_synthetic_candles


@pytest.mark.parametrize("price", [float("nan"), float("inf"), 0.0, -1.0])
def test_guarded_backtest_rejects_invalid_fill_price(price):
    broker = SimBroker(100, leverage=5)
    broker.submit([Signal(side="long", strength=0.4)], open_risk_limit=(200, 0.1))
    broker.fill_pending(price, 1000, 0)
    assert broker.pos.side == "flat"
    assert broker.trades == []


@pytest.mark.parametrize("cash,price,expected", [(200, 120, 0.25), (50, 80, 0.5)])
async def test_backtest_fill_respects_frozen_risk_cap_after_equity_change(
        monkeypatch, cash, price, expected):
    from app.ai_auto.sizing_runtime import SizingContext

    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(200)
        settings = _sizing_settings(ai_trade_vol_target_annual=0)
        _patch_loaders(monkeypatch, candles, _StubLLM(_alternating))
        runner = AiBacktestRunner(_spec(sid, paper, candles, settings))
        runner.main.cash = 100
        decision = TradeDecision(action="open_long", size_fraction=0.5,
                                 confidence=0.8, reason="测试")
        final, _, _ = runner._sizing.resolve(decision, SizingContext(
            step=0.25, ref_px=100, notional_full=100 * runner.leverage))
        runner._execute(final, ts=1000)
        runner.main.cash = cash
        runner.main.fill_pending(price, 2000, 1)
        trade = runner.main.trades[-1]
        assert trade.qty * trade.price == pytest.approx(cash * runner.leverage * expected)
        assert trade.qty * trade.price <= 50 * runner.leverage + 1e-10
    finally:
        await dispose_engine()


class _StubLLM:
    def __init__(self, decide_fn):
        self._decide_fn = decide_fn
        self.decision_calls = 0

    async def stream(self, *, system, messages, tools):
        if not tools:
            yield StreamEvent("text_delta", text="综合执行摘要")
            yield StreamEvent("message_done", stop_reason="end")
            return
        self.decision_calls += 1
        args = self._decide_fn(self.decision_calls)
        yield StreamEvent("tool_use_start", id="t", name="submit_trade_decision")
        yield StreamEvent("tool_use_args_delta", id="t", partial_json=json.dumps(args))
        yield StreamEvent("message_done", stop_reason="tool_use")


def _alternating(call: int) -> dict:
    if call % 2 == 1:
        return {"action": "open_long", "sizeFraction": 1.0, "confidence": 0.8, "reason": f"r{call}"}
    return {"action": "close", "reason": f"r{call}"}


async def _seed_spec():
    await init_db()
    async with get_sessionmaker()() as s:
        sid = (await s.execute(select(Strategy.id).order_by(Strategy.id.asc()).limit(1))).scalar_one()
        row = await repo.get_strategy_row(s, sid)
    return sid, build_paper_strategy_spec(row)


def _spec(sid, paper_spec, candles, settings, *, every=40) -> AiBacktestSpec:
    return AiBacktestSpec(
        inst_id="BTC-USDT-SWAP", bar="1H", strategy_ids=[sid], strategy_names=["S"],
        specs=[paper_spec], start_ts=candles[60]["ts"], end_ts=candles[-1]["ts"],
        decision_every_bars=every, initial_capital=10000.0, leverage=3.0,
        stop_loss_pct=0.05, take_profit_pct=0.1, take_profit_usd=None, stop_loss_usd=None,
        context_refresh_rounds=3, min_consensus=0, planned_rounds=6,
        settings=settings, rest=object(), provider=object())


def _patch_loaders(monkeypatch, candles, llm):
    async def fake_range(*_a, **_k):
        return candles

    async def fake_htf(*_a, **_k):
        return {}

    monkeypatch.setattr("app.ai_auto.backtest.load_range_candles", fake_range)
    monkeypatch.setattr("app.ai_auto.backtest.load_htf_frames", fake_htf)
    monkeypatch.setattr("app.ai_auto.backtest.make_llm_client_from_config", lambda cfg: llm)


def _sizing_settings(**over) -> Settings:
    base = {"ai_trade_stop_atr_mult": 1.5, "ai_trade_sizing_enabled": True,
            "ai_trade_realized_vol_window": 5, "ai_trade_correlation_min_samples": 5}
    base.update(over)
    return Settings(**base)


# ---------- T-19（复发点一）：总闸②必须能独立触发 `_atr_series` 的构造 ----------
async def test_backtest_atr_series_built_for_stop(monkeypatch):
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(200)
        settings = _sizing_settings(ai_trade_profit_guard_backtest_enabled=False)
        assert settings.ai_trade_edge_gate_k_min == 0.0        # 既无保护层、k_min 又是 0
        _patch_loaders(monkeypatch, candles, _StubLLM(_alternating))
        runner = AiBacktestRunner(_spec(sid, paper, candles, settings))
        job = _BacktestJob("aibt_atr_series", runner)
        await runner.run(job)
        assert runner._atr_series is not None                  # 只靠总闸②也必须建出这条序列
        assert runner._atr_at(100) is not None
    finally:
        await dispose_engine()


async def test_atr_series_absent_when_all_three_switches_off(monkeypatch):
    """反向锚点：三个总闸全关时序列**仍然**不建（本轮不改变既有行为，零回归）。"""
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(200)
        settings = Settings(ai_trade_profit_guard_backtest_enabled=False)
        _patch_loaders(monkeypatch, candles, _StubLLM(_alternating))
        runner = AiBacktestRunner(_spec(sid, paper, candles, settings))
        await runner.run(_BacktestJob("aibt_no_series", runner))
        assert runner._atr_series is None
    finally:
        await dispose_engine()


# ---------- T-19b（复发点二 [P0-3]）：ATR 止损**真的改变了成交价** ----------
async def test_backtest_atr_stop_actually_moves_fill(monkeypatch):
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(300)
        _patch_loaders(monkeypatch, candles, _StubLLM(_alternating))
        runner = AiBacktestRunner(_spec(sid, paper, candles, _sizing_settings(), every=20))
        await runner.run(_BacktestJob("aibt_atr_stop", runner))
        opens = [t for t in runner.main.trades if t.side in ("long", "short")
                 and t.stop_loss_price is not None]
        assert len(opens) >= 2
        ratios = {round(t.stop_loss_price / t.price, 8) for t in opens}
        # 不同根的 ATR 不同 ⇒ 冻结进 pos 的止损价/开仓价比值必须不同。只改 `_oco_risk`（不调 set_risk）
        # 的写法会让这个集合退化成一个固定常数——那正是本用例要判红的静默失效。
        assert len(ratios) > 1, "ATR 止损没有真的改变成交价：双写缺了 main.set_risk(...)"
        assert all(abs(r - 0.95) > 1e-9 for r in ratios), "止损仍是构造期的固定 5%"
    finally:
        await dispose_engine()


# ---------- 轴 B（tradability-and-open-funnel）：回测侧必须同步，否则两侧止损位系统性分叉 ----------
async def test_backtest_feeds_cost_bps_into_the_sizing_context(monkeypatch):
    """D-8 的**本轮那一半**：`SizingContext.cost_bps` 补齐（回测世界常数）。

    **两个反直觉后果**（实现节点必须知道）：
    1. 补 `cost_bps` 对回测的**仓位**是**零行为变化**——`kelly_fraction` 先判 `p_win is None`
       ⇒ `no_prior` 短路，`cost_bps` 的检查在其之后；而回测侧没有 `PriorRuntime`，`p_win` 恒
       `None`。**若你期待仓位数字变化、结果没变，那不是没接上，那是对的。**
    2. 但它是**轴 B 在回测侧生效的必要条件**：不补则 `ctx.cost_bps` 恒 `None` ⇒ 止损下界分支恒
       不进 ⇒ 回测与实盘的止损位系统性分叉（「双通道共用纯核心时只有一侧被实现」的成文缺陷模式）。
    """
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(200)
        _patch_loaders(monkeypatch, candles, _StubLLM(_alternating))
        runner = AiBacktestRunner(_spec(sid, paper, candles, _sizing_settings()))
        seen: list = []
        original = runner._sizing.resolve

        def _spy(decision, ctx, executor=None):
            seen.append(ctx.cost_bps)
            return original(decision, ctx, executor)

        runner._sizing.resolve = _spy
        await runner.run(_BacktestJob("aibt_cost_bps", runner))
        assert seen, "回测一轮都没跑到仓位求解"
        opens = [value for value in seen if value is not None]
        assert opens, "开仓轮的 cost_bps 恒为 None ⇒ 止损下界在回测侧恒不生效"
        assert all(value > 0 for value in opens)
        # 非开仓轮（hold / close）不该凭空造一个成本——方向未知时它不是一个有意义的数。
        assert any(value is None for value in seen)
    finally:
        await dispose_engine()


async def test_backtest_stop_floor_binds_when_atr_is_narrower_than_cost(monkeypatch):
    """轴 B 在回测侧真的绑定：把下界倍数拉到值域上限（20）+ 把 ATR 倍数压到极小 ⇒ 下界必然咬合。"""
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(200)
        _patch_loaders(monkeypatch, candles, _StubLLM(_alternating))
        settings = _sizing_settings(ai_trade_stop_cost_floor_mult=20.0,
                                    ai_trade_stop_atr_mult=0.01)
        runner = AiBacktestRunner(_spec(sid, paper, candles, settings))
        await runner.run(_BacktestJob("aibt_stop_floor", runner))
        assert runner._sizing.counters.stop_floored_rounds > 0
        assert runner._sizing.counters.atr_stop_rounds >= (
            runner._sizing.counters.stop_floored_rounds)
    finally:
        await dispose_engine()


def test_only_writing_oco_risk_is_provably_inert():
    """对照实验：证明 T-19b 的判据真的能把「只改 `_oco_risk`」判红（G-13 的先红后绿）。"""
    broker = SimBroker(10_000.0, taker_fee_bps=5.0, slippage_bps=2.0, leverage=1.0)
    oco = RiskParams(stop_loss_pct=0.05, take_profit_pct=0.10)
    broker.set_risk(oco)
    row = {"h": 101.0, "l": 99.0, "ts": 1}
    broker.submit([Signal(side="long", strength=1.0, reason="r")])
    # 「最自然的写法」：改 `_oco_risk` 并把它传给 apply_risk——第三形参根本没被使用，故一行都不生效。
    oco = RiskParams(stop_loss_pct=0.01, take_profit_pct=0.02)
    broker.fill_pending(open_price=100.0, ts=1, bar_index=1)
    broker.apply_risk(1, row, oco)
    assert abs(broker.pos.stop_loss_price / broker.pos.entry_price - 0.95) < 1e-6
    # 只有 `set_risk` 才写得进「下一次开仓要用的快照」。
    broker.set_risk(oco)
    broker.submit([Signal(side="close", reason="c")])
    broker.fill_pending(open_price=100.0, ts=2, bar_index=2)
    broker.submit([Signal(side="long", strength=1.0, reason="r")])
    broker.fill_pending(open_price=100.0, ts=3, bar_index=3)
    assert abs(broker.pos.stop_loss_price / broker.pos.entry_price - 0.99) < 1e-6


# ---------- 回测时钟：连亏冷却按**根 ts** 计时（免责文案 ④ 的可执行版本） ----------
def test_backtest_cooldown_is_timed_by_bar_ts_not_wall_clock():
    """回测侧 `note_close` 用根 ts 开冷却，故门禁判定也**必须**用根 ts。

    两侧时钟不同源的后果是彻底静默：历史行情的根 ts 早就落在墙钟之后方，`remaining` 恒为 0 ⇒ 第三档
    在回测里永不触发，而免责文案 ④ 却宣称「连亏冷却按根 ts 计时」——一个不存在的行为被写进了披露。
    """
    settings = Settings(ai_trade_brake_loss_streak=1, ai_trade_brake_loss_cooldown_bars=4.0)
    runtime = build_sizing_runtime(settings, "BTC-USDT-SWAP", 3600)
    bar_ts = 1_600_000_000.0                    # 历史根：墙钟早已走过这个时刻
    runtime.note_close({"pnl": -10.0}, now_wall=bar_ts)
    decision = TradeDecision(action="open_long", size_fraction=0.5, confidence=0.6, reason="r")
    halted, reason = runtime.gate(decision, None, now_wall=bar_ts + 3600.0)
    assert halted.action == "hold"
    assert reason is not None and reason.startswith("loss_streak_cooldown(")
    # 冷却按根 ts 到期后照常放行（4 根 × 3600s = 14400s）。
    passed, none_reason = runtime.gate(decision, None, now_wall=bar_ts + 14401.0)
    assert passed is decision and none_reason is None


# ---------- T-20：回测与实盘的门禁调用序列一致 ----------
def test_backtest_same_insertion_order():
    """刹车在冷却之后、共识之前；仓位求解在反思之后、`_execute` 之前——与实盘管线同序。"""
    import inspect

    from app.ai_auto import backtest as backtest_module
    from app.ai_auto import session as session_module

    def _order(source: str, marks: dict[str, str]) -> list[str]:
        hits = [(source.index(needle), name) for name, needle in marks.items()
                if needle in source]
        return [name for _, name in sorted(hits)]

    bt = inspect.getsource(backtest_module.AiBacktestRunner._run_decision)
    live = inspect.getsource(session_module.AiTradeSession._run_decision_round)
    assert _order(bt, {"cooldown": "apply_profit_cooldown(", "brake": "_sizing.gate(",
                       # directional-symmetry 轴 A：回测侧改调 `resolve_consensus_gate`
                       # （与实盘**同一个纯核心**）；插入位置一个字没动。
                       "consensus": "resolve_consensus_gate(", "edge": "evaluate_edge_gate(",
                       "reflect": "apply_reflection(", "sizing": "_sizing.resolve(",
                       "execute": "self._execute("}) == [
        "cooldown", "brake", "consensus", "edge", "reflect", "sizing", "execute"]
    assert _order(live, {"cooldown": "_apply_profit_cooldown_gate(", "brake": "_sizing.gate(",
                         "consensus": "_apply_consensus_gate(", "edge": "_apply_edge_gate(",
                         "reflect": "_reflect_before_execute(", "sizing": "_apply_sizing(",
                         "execute": "_execute_decision("}) == [
        "cooldown", "brake", "consensus", "edge", "reflect", "sizing", "execute"]


# ---------- 免责文案：装配即下发，六条偏差逐条可见 ----------
async def test_backtest_note_lists_all_six_deviations(monkeypatch):
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(200)
        _patch_loaders(monkeypatch, candles, _StubLLM(_alternating))
        runner = AiBacktestRunner(_spec(sid, paper, candles, _sizing_settings()))
        job = _BacktestJob("aibt_note", runner)
        await runner.run(job)
        assert SIZING_BACKTEST_NOTE in job.result["notes"]
        for mark in ("①", "②", "③", "④", "⑤", "⑥"):
            assert mark in SIZING_BACKTEST_NOTE
        assert "单标的" in SIZING_BACKTEST_NOTE and "no_prior" in SIZING_BACKTEST_NOTE
    finally:
        await dispose_engine()


async def test_backtest_note_absent_when_layer_off(monkeypatch):
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(200)
        _patch_loaders(monkeypatch, candles, _StubLLM(_alternating))
        runner = AiBacktestRunner(_spec(sid, paper, candles, Settings()))
        job = _BacktestJob("aibt_note_off", runner)
        await runner.run(job)
        assert SIZING_BACKTEST_NOTE not in job.result["notes"]
        assert runner._sizing.armed is False
    finally:
        await dispose_engine()
