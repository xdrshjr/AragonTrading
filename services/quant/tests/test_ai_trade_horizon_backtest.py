"""持仓周期铁律层的回测同构测试（ai-trade-holding-horizon-modes §6.1 · backtest 组）。

三条承重断言，每条都是「照最自然的写法实现就会红」的那种：

- **同核心同判定**（G-3）：回测与实盘在同一断面下产出**逐字相等**的 reason 字符串；
- **bar 时钟真的接上了**（`_track_horizon_epoch`）：若依赖 `refresh()` 兜底建 epoch，
  `openedWallEstimated` 会恒为真 ⇒ `epoch_estimated` 豁免每轮命中 ⇒ **G1 在回测里恒不生效且不报错**；
- **前瞻窗口两侧同源**（[P1-3]）：`backtest_params(settings, expected_hold_bars=k)` 的资金费期数
  必须随 k 变，否则回测按 8 根算、实盘按 12–72 根算，G-3 不可能通过。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.agent.llm.base import StreamEvent
from app.ai_auto.backtest import AiBacktestRunner, AiBacktestSpec, _BacktestJob
from app.ai_auto.cost_model import CostQuery, backtest_params, funding_periods
from app.ai_auto.decider import TradeDecision
from app.ai_auto.hold_horizon import (
    HorizonFacts,
    HorizonRule,
    HorizonState,
    build_snapshot,
    evaluate_horizon_gate,
)
from app.ai_auto.horizon_runtime import HorizonRuntime
from app.config import Settings
from app.db import repositories as repo
from app.db.models import Strategy
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.paper.strategy_factory import build_paper_strategy_spec

from tests.conftest import make_synthetic_candles

_RULE = HorizonRule(profile_id="intraday_swing", title="日内波段", target_label="4 – 12 小时",
                    min_hold_s=7200.0, exit_confirm_rounds=2, confirm_window_s=3600.0,
                    hysteresis_k=1.5, target_hold_min_s=14400.0, target_hold_max_s=43200.0,
                    expected_hold_bars=12)


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
    """开一根、下一根就想平——正是本层要治的抢跑退出模式。"""
    if call % 2 == 1:
        return {"action": "open_long", "sizeFraction": 1.0, "confidence": 0.8,
                "reason": f"r{call}"}
    return {"action": "close", "reason": f"r{call}"}


async def _seed_spec():
    await init_db()
    async with get_sessionmaker()() as s:
        sid = (await s.execute(
            select(Strategy.id).order_by(Strategy.id.asc()).limit(1))).scalar_one()
        row = await repo.get_strategy_row(s, sid)
    return sid, build_paper_strategy_spec(row)


def _spec(sid, paper_spec, candles, settings, *, horizon_rule, every=2) -> AiBacktestSpec:
    return AiBacktestSpec(
        inst_id="BTC-USDT-SWAP", bar="1H", strategy_ids=[sid], strategy_names=["S"],
        specs=[paper_spec], start_ts=candles[60]["ts"], end_ts=candles[-1]["ts"],
        decision_every_bars=every, initial_capital=10000.0, leverage=3.0,
        stop_loss_pct=0.05, take_profit_pct=0.1, take_profit_usd=None, stop_loss_usd=None,
        context_refresh_rounds=50, min_consensus=0, planned_rounds=20,
        settings=settings, rest=object(), provider=object(), horizon_rule=horizon_rule)


def _patch_loaders(monkeypatch, candles, llm):
    async def fake_range(*_a, **_k):
        return candles

    async def fake_htf(*_a, **_k):
        return {}

    monkeypatch.setattr("app.ai_auto.backtest.load_range_candles", fake_range)
    monkeypatch.setattr("app.ai_auto.backtest.load_htf_frames", fake_htf)
    monkeypatch.setattr("app.ai_auto.backtest.make_llm_client_from_config", lambda cfg: llm)


async def _run(monkeypatch, *, horizon_rule, settings=None, count=200):
    sid, paper = await _seed_spec()
    candles = make_synthetic_candles(count)
    settings = settings or Settings(ai_trade_funding_enabled=False)
    _patch_loaders(monkeypatch, candles, _StubLLM(_alternating))
    runner = AiBacktestRunner(_spec(sid, paper, candles, settings, horizon_rule=horizon_rule))
    await runner.run(_BacktestJob("aibt_horizon", runner))
    return runner


async def test_backtest_gate_blocks_the_immediate_close(monkeypatch):
    """G1 在回测里**真的生效**：1H 执行 + 2h 最短窗口 ⇒ 开仓次根的 close 被拦（计数 > 0）。"""
    try:
        runner = await _run(monkeypatch, horizon_rule=_RULE)
        stats = runner._aggregate_stats()["horizon"]
        assert stats is not None
        assert stats["minHoldVetoes"] > 0, "回测里 G1 恒不生效——多半是 epoch 走了估算兜底"
    finally:
        await dispose_engine()


async def test_backtest_epoch_uses_bar_ts_not_estimated(monkeypatch):
    """`opened_wall` 取**开仓根 ts**；若靠 `refresh()` 兜底建 epoch，G1 会被 epoch_estimated 全解。"""
    try:
        runner = await _run(monkeypatch, horizon_rule=_RULE)
        assert runner._bar_ts_index is not None       # 契约装配也必须建这条索引
        snapshot = runner._horizon.last_snapshot
        assert snapshot is not None
        assert snapshot["releaseReason"] != "epoch_estimated"
    finally:
        await dispose_engine()


async def test_backtest_and_live_produce_identical_reason(monkeypatch):
    """G-3 同核心同判定：同一断面下两侧的 reason **逐字相等**（本就是同一个纯函数）。"""
    facts = HorizonFacts(seq=7, side="long", entry_px=100.0, mark_px=100.05, leverage=3.0,
                         now_wall=1_700_000_600.0, cost_bps=13.0)
    state = HorizonState(epoch=("long", 7), opened_wall=1_700_000_000.0,
                         exit_confirm_streak=2)
    snapshot = build_snapshot(_RULE, state, facts)
    _, reason = evaluate_horizon_gate(TradeDecision("close", None, 0.7, "x"), snapshot,
                                      _RULE, position_side="long")
    assert reason == "horizon_min_hold(held=10m,min=120m)"


def test_track_horizon_epoch_closes_the_previous_epoch_on_reverse():
    """反手在**同一根**里换了持仓身份、中途不经过 flat：不先收一笔 `note_close`，上一段持仓的时长
    样本与 `roundTrips` 会静默消失——而「平均持仓时间 / 每日换手次数」正是本特性的验收指标。

    实盘侧无此问题（`_dispatch` 的 close 与 open 是两条成交事件，天然成对），故只需在回测这一侧守。
    """
    runtime = HorizonRuntime(_RULE)
    stub = SimpleNamespace(horizon_rule=_RULE, _horizon=runtime,
                           main=SimpleNamespace(pos=SimpleNamespace(side="long", entry_bar=10)),
                           _bar_ts=lambda index, default=0: 1_700_000_000 + index * 3600)
    AiBacktestRunner._track_horizon_epoch(stub, 1_700_036_000)          # 开多
    stub.main.pos = SimpleNamespace(side="short", entry_bar=20)         # 同根反手 ⇒ 直接换身份
    AiBacktestRunner._track_horizon_epoch(stub, 1_700_072_000)
    stats = runtime.stats()
    assert stats["roundTrips"] == 1, "反手漏收样本：多头那一段持仓从统计里凭空消失"
    assert stats["avgHoldSeconds"] == pytest.approx(36_000.0)
    assert runtime.state.epoch == ("short", 20)
    assert runtime.state.opened_wall_estimated is False


def test_backtest_params_honour_expected_hold_bars():
    """[P1-3]：回测世界的成本常数同步开口——期数随 `expected_hold_bars` 变，否则两侧不可能对齐。"""
    settings = Settings()
    query = CostQuery(ord_type="market", side="long", bar_seconds=3600)
    assert funding_periods(query, backtest_params(settings)) == 1          # 8 根 × 1H < 8h
    assert funding_periods(query, backtest_params(settings, expected_hold_bars=48)) == 6
    # 缺省仍逐字回退全局常数（既有调用点零回归）。
    assert backtest_params(settings) == backtest_params(settings, expected_hold_bars=None)


async def test_backtest_notes_name_the_mode_when_window_below_one_bar(monkeypatch):
    """[P2-7]：最短窗口小于一根 K 线时**必须点名该模式**，否则用户会拿这份回测为实盘的 G1 背书。"""
    try:
        rule = HorizonRule(profile_id="hourly_swing", title="小时波段", min_hold_s=180.0,
                           exit_confirm_rounds=2, confirm_window_s=90.0, hysteresis_k=1.2,
                           target_hold_min_s=360.0, target_hold_max_s=7200.0,
                           expected_hold_bars=2)
        runner = await _run(monkeypatch, horizon_rule=rule)
        notes = " ".join(runner._build_notes(
            __import__("pandas").DataFrame({"ts": [0]}), 0))
        assert "hourly_swing" in notes and "G1 恒不生效" in notes
    finally:
        await dispose_engine()


async def test_backtest_unarmed_is_a_no_op(monkeypatch):
    """零回归：未装配 ⇒ `aiStats.horizon` 为 None、notes 无本层免责、payload 无该键。"""
    try:
        runner = await _run(monkeypatch, horizon_rule=None)
        assert runner._aggregate_stats()["horizon"] is None
        notes = " ".join(runner._build_notes(
            __import__("pandas").DataFrame({"ts": [0]}), 0))
        assert "交易周期模式" not in notes
        assert runner._run_config()["tradingHorizon"] is None
    finally:
        await dispose_engine()
