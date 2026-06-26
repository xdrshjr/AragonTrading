"""组合引擎单测（engine/combo.py）：因果性、单成员等价、体制驱动切换、指标形状、年化守卫。

核心先行（TDD）：``test_combo_causality_no_lookahead`` 固化「无未来函数」硬纪律。
"""
from __future__ import annotations

import math

import pytest

from app.engine.backtester import run_backtest
from app.engine.combo import (
    ComboMember,
    ComboPolicy,
    ComboSegment,
    _annualized_return,
    _combo_trades,
    _warmup_floor,
    run_combo_backtest,
)
from app.strategies.base import get_strategy_class
from app.engine.simulator import TradeRecord
from tests.conftest import make_synthetic_candles

_COSTS = dict(initial_capital=10_000.0, taker_fee_bps=5.0, slippage_bps=2.0, leverage=1.0)


def _member(template_key: str, params: dict | None = None) -> ComboMember:
    cls = get_strategy_class(template_key)
    strategy = cls(params or {})
    return ComboMember(key=template_key, template_key=template_key, name=cls.meta.name,
                       strategy=strategy, timeframes=list(strategy.timeframes()))


def _ohlc(ts: int, close: float) -> dict:
    return {"ts": ts, "o": close, "h": close * 1.002, "l": close * 0.998, "c": close, "vol": 1000.0}


def _trend_then_range(n_trend: int = 170, n_range: int = 170,
                      start_ts: int = 1_700_000_000, step: int = 3600) -> list[dict]:
    """先强单边上行、后区间震荡 —— 让体制分类器分别判为 trend_up / range。"""
    candles: list[dict] = []
    ts = start_ts
    price = 100.0
    for _ in range(n_trend):
        price *= 1.01
        candles.append(_ohlc(ts, price))
        ts += step
    base = price
    for i in range(n_range):
        candles.append(_ohlc(ts, base * (1.0 + 0.01 * math.sin(i / 3.0))))
        ts += step
    return candles


def test_combo_causality_no_lookahead():
    """篡改第 cut 根之后的全部 K 线，断言 equity[:cut+1] 逐点不变 → 证明无未来函数。"""
    candles = make_synthetic_candles(300)
    policy = ComboPolicy(rebalance_bars=12, lookback_bars=48, min_dwell_bars=6, regime_gating=False)
    members = [_member("ema_cross_scalper", {"htf_trend_filter": False}),
               _member("range_scalping_strategy")]
    base = run_combo_backtest(members, candles, "1H", htf_frames_by_member={}, policy=policy, **_COSTS)

    cut = 220
    tampered = [dict(c) for c in candles]
    for i in range(cut + 1, len(tampered)):
        tampered[i]["c"] *= 1.5
        tampered[i]["h"] *= 1.7
        tampered[i]["l"] *= 0.8
    members2 = [_member("ema_cross_scalper", {"htf_trend_filter": False}),
                _member("range_scalping_strategy")]
    tamp = run_combo_backtest(members2, tampered, "1H", htf_frames_by_member={}, policy=policy, **_COSTS)

    assert len(base.equity_points) == len(tamp.equity_points) == 300
    for i in range(cut + 1):
        assert base.equity_points[i].ts == tamp.equity_points[i].ts
        assert base.equity_points[i].equity == pytest.approx(tamp.equity_points[i].equity, abs=1e-6)


def test_combo_single_member_equals_member():
    """单成员 + turnoverBps=0 + 无体制门控 → 组合净值与该成员单策略在重叠区一致（退化等价）。"""
    candles = make_synthetic_candles(300)
    policy = ComboPolicy(rebalance_bars=12, lookback_bars=30, min_dwell_bars=1,
                         regime_gating=False, turnover_bps=0.0)
    member = _member("ema_cross_scalper", {"htf_trend_filter": False})
    combo = run_combo_backtest([member], candles, "1H", htf_frames_by_member={}, policy=policy, **_COSTS)

    fresh = get_strategy_class("ema_cross_scalper")({"htf_trend_filter": False})
    member_run = run_backtest(fresh, candles, "1H", initial_capital=10_000.0,
                              taker_fee_bps=5.0, slippage_bps=2.0, leverage=1.0)
    wf = _warmup_floor([member], policy, None)
    member_eq = member_run.equity_points
    ratio_combo = combo.equity_points[-1].equity / 10_000.0
    ratio_member = member_eq[-1].equity / member_eq[wf - 1].equity
    assert ratio_combo == pytest.approx(ratio_member, rel=1e-6)


def test_combo_switches_with_regime():
    """先趋势后区间 + trend/range 成员 + 体制门控 → 活跃成员随体制切换（两成员都出现）。"""
    candles = _trend_then_range()
    policy = ComboPolicy(rebalance_bars=12, lookback_bars=48, min_dwell_bars=1, regime_gating=True)
    members = [_member("trend_pullback_long"), _member("range_scalping_strategy")]
    result = run_combo_backtest(members, candles, "1H", htf_frames_by_member={}, policy=policy, **_COSTS)

    active_keys = {seg.member_key for seg in result.segments}
    assert "trend_pullback_long" in active_keys
    assert "range_scalping_strategy" in active_keys
    # 抗 whipsaw：切换次数（=段数-1）有上界，远小于决策次数。
    assert len(result.segments) <= len(candles) // policy.rebalance_bars + 1


def test_combo_metrics_and_equity_shape():
    """metrics 七键齐全；equity ts 严格递增且唯一；组合成交 ts 落在对应活跃段内。"""
    candles = make_synthetic_candles(300)
    policy = ComboPolicy(rebalance_bars=12, lookback_bars=48, min_dwell_bars=6, regime_gating=True)
    members = [_member("trend_pullback_long"), _member("range_scalping_strategy"),
               _member("trend_pullback_short")]
    result = run_combo_backtest(members, candles, "1H", htf_frames_by_member={}, policy=policy, **_COSTS)

    assert set(result.metrics.keys()) == {
        "totalReturn", "winRate", "profitFactor", "maxDrawdown", "sharpe", "tradeCount", "avgPnl"}
    ts_list = [p.ts for p in result.equity_points]
    assert ts_list == sorted(ts_list)
    assert len(ts_list) == len(set(ts_list))
    ranges = [(s.start_ts, s.end_ts) for s in result.segments]
    for trade in result.trades:
        assert any(lo <= trade.ts <= hi for lo, hi in ranges)


def test_combo_trades_preserve_member_risk_snapshots():
    trade = TradeRecord(
        ts=100,
        side="long",
        price=100.0,
        qty=1.0,
        fee=0.0,
        pnl=0.0,
        position_after=1.0,
        reason="entry",
        stop_loss_pct=0.02,
        take_profit_pct=0.04,
        max_hold_bars=12,
        stop_loss_price=98.0,
        take_profit_price=104.0,
    )
    segment = ComboSegment(
        start_ts=0,
        end_ts=200,
        member_key="member-a",
        member_name="Member A",
        regime="range",
        bars=10,
        contribution=0.0,
    )

    [copied] = _combo_trades({"member-a": [trade]}, [segment])

    assert copied.reason == "member-a:entry"
    assert copied.stop_loss_pct == 0.02
    assert copied.take_profit_pct == 0.04
    assert copied.max_hold_bars == 12
    assert copied.stop_loss_price == 98.0
    assert copied.take_profit_price == 104.0


def test_combo_annualized_return_guards():
    """年化守卫（R3）：零有效根数→0；负底数（杠杆全损）→-1；正常底数得有限实数。"""
    assert _annualized_return(0.5, "1H", 0) == 0.0
    assert _annualized_return(-1.5, "1H", 100) == -1.0
    one_year = _annualized_return(0.1, "1H", 8760)
    assert one_year == pytest.approx(0.1, rel=1e-6)
    assert math.isfinite(_annualized_return(-0.99, "4H", 50))


def test_combo_empty_candles_no_crash():
    """空 K 线 → 空净值/空时间线 + 零指标（不假数据、不崩）。"""
    members = [_member("ema_cross_scalper", {"htf_trend_filter": False}),
               _member("range_scalping_strategy")]
    result = run_combo_backtest(members, [], "1H", htf_frames_by_member={},
                                policy=ComboPolicy(), **_COSTS)
    assert result.equity_points == []
    assert result.segments == []
    assert result.metrics["totalReturn"] == 0.0
    assert result.annualized_return == 0.0
