"""回测车道的四条承重（discovery-strategy-axes §2.5）。

- **BL-1**：成本传 **bps 原值**（``run_backtest`` 内部才 ``/1e4``）；反向：因子轴的
  ``round_trip_cost`` 必须继续返回**小数**（防止有人「统一口径」把上游改坏）。
- **BL-2**：两个独立策略实例；OOS 前伸 pad 只暖机不交易；gate 泄漏即拒。
- **BL-3**：rebase 切片后 drawdown 从切片峰值起算；起点权益 ≤0 时返回 ``None`` 而非除零。
- **BL-13**：``metrics_of`` 的返回**必须含 calmar 与 tradeRate**——这条就是「只调
  ``compute_metrics`` ⇒ 全员卡 S5」的机器判据。
"""
from __future__ import annotations

import app.discovery.backtest_lane as backtest_lane
from app.config import Settings
from app.discovery.backtest_lane import (
    GatedStrategy,
    ParamAssembler,
    clip_rebased,
    clip_result,
    costs_of,
    metrics_of,
    run_split_backtest,
    warmup_pad,
)
from app.discovery.validation import round_trip_cost
from app.engine.simulator import EquityRecord

from .discovery_synthetic import lane_context, trend_candles

_TEMPLATE = "momentum_breakout"
_PARAMS = {"surge_pct": 0.02, "breakout_window": 20, "vol_mult": 1.2, "trail_pct": 0.02,
           "stop_loss_pct": 0.03, "max_hold_bars": 48, "strength": 0.9}


# ============================ BL-1：成本量纲（双向） ============================
def test_costs_are_passed_as_bps_not_decimals(monkeypatch):
    """承重：收到的必须是 ``5.0``（bps），不是 ``0.0005``（小数）。"""
    seen: list[dict] = []
    original = backtest_lane.run_backtest

    def _spy(strategy, candles, bar, **kwargs):
        seen.append(dict(kwargs))
        return original(strategy, candles, bar, **kwargs)

    monkeypatch.setattr(backtest_lane, "run_backtest", _spy)
    ctx = lane_context(trend_candles(500))
    run_split_backtest(ParamAssembler(_TEMPLATE, _PARAMS), ctx)
    assert seen and seen[0]["taker_fee_bps"] == 5.0
    assert seen[0]["slippage_bps"] == 2.0
    assert seen[0]["initial_capital"] == 10000.0


def test_factor_axis_round_trip_cost_still_returns_a_decimal():
    """反向门禁：上游 F8 的 ``round_trip_cost`` 必须继续 ``/1e4``（两处方向相反）。"""
    assert abs(round_trip_cost(5.0, 2.0) - 0.0014) < 1e-12
    assert costs_of(Settings())[1] == 5.0


def test_stress_multiplier_scales_only_the_cost_terms(monkeypatch):
    seen: list[dict] = []
    original = backtest_lane.run_backtest
    monkeypatch.setattr(backtest_lane, "run_backtest",
                        lambda s, c, b, **kw: (seen.append(dict(kw)) or
                                               original(s, c, b, **kw)))
    ctx = lane_context(trend_candles(500))
    backtest_lane.run_stress_is(ParamAssembler(_TEMPLATE, _PARAMS), ctx, cost_mult=2.0)
    assert seen[-1]["taker_fee_bps"] == 10.0 and seen[-1]["slippage_bps"] == 4.0
    assert seen[-1]["initial_capital"] == 10000.0


# ============================ BL-2：IS/OOS 双段 ============================
def test_is_and_oos_use_two_independent_strategy_instances(monkeypatch):
    """复用同一实例会让 IS 末尾的跟踪止损 / 持仓运行态泄漏进 OOS。

    判据取**真正喂进 ``run_backtest`` 的那两个对象**（对象在断言期间仍被引用持有，
    不会因地址回收而误判）。
    """
    used: list = []
    original = backtest_lane.run_backtest

    def _spy(strategy, candles, bar, **kwargs):
        used.append(strategy)
        return original(strategy, candles, bar, **kwargs)

    monkeypatch.setattr(backtest_lane, "run_backtest", _spy)
    run_split_backtest(ParamAssembler(_TEMPLATE, _PARAMS), lane_context(trend_candles(600)))
    assert len(used) == 2
    inner = used[1]._inner if isinstance(used[1], GatedStrategy) else used[1]
    assert used[0] is not inner, "IS 与 OOS 复用了同一个策略实例"
    assert isinstance(used[1], GatedStrategy), "OOS 段没有被 gate 包住"


def test_the_gate_region_produces_no_trades_and_keeps_equity_flat():
    ctx = lane_context(trend_candles(600))
    split = run_split_backtest(ParamAssembler(_TEMPLATE, _PARAMS), ctx)
    assert split.reject_reason == "" and split.clip_mode == "gated"
    assert split.pad >= warmup_pad(ParamAssembler(_TEMPLATE, _PARAMS).build())
    assert abs(float(split.oos_points[0].equity) - 10000.0) < 1e-6
    assert split.oos_points[0].ts >= ctx.split_ts


def test_gate_leak_is_rejected_fail_closed():
    """起点权益 ≠ 初始资金就是 gate 没生效——机器判据，比注释可靠。"""
    leaked = _Result([EquityRecord(ts=100, equity=10500.0, drawdown=0.0)], [])
    assert clip_result(leaked, from_ts=0, initial_capital=10000.0, bar="1H") is None


def test_gated_strategy_forwards_everything_but_signals():
    inner = ParamAssembler(_TEMPLATE, _PARAMS).build()
    gated = GatedStrategy(inner, first_index=10)
    assert gated.warmup() == inner.warmup()
    assert gated.timeframes() == inner.timeframes()
    assert gated.meta is inner.meta
    assert gated.on_candle(None, 3, None) == []


# ============================ BL-13：指标并集出口 ============================
def test_metrics_of_contains_calmar_and_trade_rate():
    """只调 ``compute_metrics`` ⇒ 拿不到 calmar ⇒ S5 把**每一条**候选都拒掉且不报错。"""
    ctx = lane_context(trend_candles(600))
    split = run_split_backtest(ParamAssembler(_TEMPLATE, _PARAMS), ctx)
    for side in (split.is_metrics, split.oos_metrics):
        assert "calmar" in side, "缺 calmar ⇒ S5 恒判不可判定即拒"
        assert "tradeRate" in side and "bars" in side
        assert "sharpe" in side and "totalReturn" in side


def test_trade_rate_is_bars_based_not_wall_clock():
    """``metrics_ext`` 的 ``tradesPerWeek`` 是按墙钟秒换算的，与 S7 的「按根数」不是一回事。"""
    points = [EquityRecord(ts=i * 3600, equity=10000.0 + i, drawdown=0.0) for i in range(50)]
    metrics = metrics_of(points, [], "1H", 10000.0)
    assert metrics["bars"] == 50 and metrics["tradeRate"] == 0.0
    assert "tradesPerWeek" in metrics, "并集出口丢了 metrics_ext 的字段"


# ============================ BL-3：rebase 切片 ============================
def test_rebased_drawdown_restarts_from_the_clipped_peak():
    points = [EquityRecord(ts=0, equity=10000.0, drawdown=0.0),
              EquityRecord(ts=1, equity=20000.0, drawdown=0.0),
              EquityRecord(ts=2, equity=12000.0, drawdown=-0.4),
              EquityRecord(ts=3, equity=13200.0, drawdown=-0.34)]
    clipped = clip_rebased(_Result(points, []), from_ts=2, initial_capital=10000.0, bar="1H")
    assert clipped is not None
    assert abs(clipped.points[0].equity - 10000.0) < 1e-6
    assert clipped.points[0].drawdown == 0.0, "IS 段的回撤被记到了 OOS 头上"
    assert clipped.metrics["maxDrawdown"] == 0.0


def test_rebase_returns_none_instead_of_dividing_by_zero():
    """评审 P2-5：切片为空、或起点权益已归零/为负（爆仓）时一律 fail-closed。"""
    wiped = [EquityRecord(ts=2, equity=0.0, drawdown=-1.0)]
    assert clip_rebased(_Result(wiped, []), from_ts=0, initial_capital=10000.0,
                        bar="1H") is None
    assert clip_rebased(_Result([], []), from_ts=0, initial_capital=10000.0,
                        bar="1H") is None


class _Result:
    """最小回测产物替身（只带切片需要的两组数据）。"""

    def __init__(self, equity_points: list, trades: list):
        self.equity_points = equity_points
        self.trades = trades
