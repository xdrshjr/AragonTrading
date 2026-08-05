"""策略硬闸 S1–S9 的**双向**用例（discovery-strategy-axes §2.6）。

每条闸都要有「必过」与「必拒」各一条：只有拒的用例证明不了闸没被写成恒假，只有过的用例
证明不了闸没被写成恒真。S6 另加一条真回测用例——它是本轮最强的样本外防线，也是最容易被
实现成装饰的一条。
"""
from __future__ import annotations

import app.discovery.backtest_lane as backtest_lane
from app.discovery.strategy_gates import (
    DEFAULT_STRATEGY_THRESHOLDS,
    StrategyKeptEntry,
    check_gates,
    check_stress,
    light_revalidate,
    max_return_corr,
    normalize_strategy_thresholds,
    required_sharpe,
)
from app.discovery.validation import penalized_gate

from .discovery_synthetic import lane_context, two_regime_candles

_PASS_IS = {"tradeCount": 40, "totalReturn": 0.35, "sharpe": 1.6, "maxDrawdown": -0.15,
            "calmar": 1.9, "profitFactor": 1.5, "tradeRate": 0.02, "bars": 2000}
_PASS_OOS = {"tradeCount": 12, "totalReturn": 0.12, "sharpe": 1.1, "maxDrawdown": -0.10,
             "calmar": 1.2, "profitFactor": 1.3, "tradeRate": 0.02, "bars": 800}


def _check(is_over: dict | None = None, oos_over: dict | None = None, *, trials: int = 1,
           pool: list | None = None, returns: list | None = None, thresholds=None):
    return check_gates({**_PASS_IS, **(is_over or {})}, {**_PASS_OOS, **(oos_over or {})},
                       thresholds=normalize_strategy_thresholds(thresholds), trials=trials,
                       pool=pool or [], returns=returns or [0.001, -0.002, 0.003, 0.001])


def test_all_gates_pass_on_a_healthy_candidate():
    report = _check()
    assert report.reason == ""
    assert [gate["id"] for gate in report.gates] == ["S1", "S2", "S3", "S4", "S5", "S6",
                                                     "S7", "S8"]
    assert all(gate["passed"] for gate in report.gates)


def test_s1_rejects_thin_samples_on_either_side():
    assert _check({"tradeCount": 5}).reason == "S1"
    assert _check(oos_over={"tradeCount": 1}).reason == "S1"


def test_s2_rejects_in_sample_loss():
    assert _check({"totalReturn": -0.01}).reason == "S2"


def test_s3_uses_the_shared_penalised_gate():
    """BL-4：S3 与因子轴 F3/F11 共用**一个** ``penalized_gate`` 实现。"""
    thresholds = normalize_strategy_thresholds(None)
    assert required_sharpe(1000, thresholds) == max(
        0.8, penalized_gate(0.8, 1000, 0.15))
    assert _check({"sharpe": 0.9}, trials=100000).reason == "S3"
    assert _check({"sharpe": 0.9}, trials=1).reason == ""


def test_s4_rejects_drawdown_over_the_cap():
    assert _check({"maxDrawdown": -0.9}).reason == "S4"
    assert _check({"maxDrawdown": -0.34}).reason == ""


def test_s5_needs_calmar_which_only_the_extended_metrics_provide():
    """缺 calmar ⇒ ``S5:calmar_undefined``（BL-13 存在的理由）。"""
    assert _check({"calmar": 0.2}).reason == "S5"
    report = check_gates({**_PASS_IS, "calmar": None}, _PASS_OOS,
                         thresholds=normalize_strategy_thresholds(None), trials=1, pool=[],
                         returns=[0.0, 0.1])
    assert report.reason == "S5:calmar_undefined"


def test_s6_rejects_out_of_sample_collapse_and_accepts_a_stable_pair():
    assert _check(oos_over={"sharpe": -0.2}).reason == "S6"
    assert _check(oos_over={"totalReturn": -0.05}).reason == "S6"
    assert _check(oos_over={"sharpe": 0.2}).reason == "S6"      # 留存不足（0.4 × 1.6）
    assert _check(oos_over={"sharpe": 0.7}).reason == ""


def test_s7_rejects_dense_trading_and_thin_profit_factor():
    assert _check({"tradeRate": 0.5}).reason == "S7"
    assert _check({"profitFactor": 1.0}).reason == "S7"


def test_s8_rejects_a_near_duplicate_of_the_kept_pool():
    returns = [0.01, -0.02, 0.03, -0.01, 0.02]
    twin = StrategyKeptEntry(candidate_id=1, kind="strategy_param", genome_hash="a",
                             genome={}, bar_returns=list(returns), score=1.0)
    assert _check(pool=[twin], returns=returns).reason == "S8"
    other = StrategyKeptEntry(candidate_id=2, kind="strategy_param", genome_hash="b",
                              genome={},
                              bar_returns=[0.001, 0.002, 0.0015, 0.0005, 0.0025], score=1.0)
    assert _check(pool=[other], returns=returns).reason == ""


def test_max_return_corr_aligns_unequal_lengths_and_never_raises():
    entry = StrategyKeptEntry(candidate_id=1, kind="strategy_param", genome_hash="a",
                              genome={}, bar_returns=[0.01], score=1.0)
    assert max_return_corr([0.01, 0.02], [entry]) == 0.0     # 长度不足 2 ⇒ 视为不相关


def test_s9_rejects_when_doubling_costs_kills_the_edge():
    report = check_stress(_check(), {"totalReturn": -0.02})
    assert report.reason == "S9"
    assert check_stress(_check(), {"totalReturn": 0.05}).reason == ""
    assert check_stress(_check(), {"totalReturn": None}).reason == "S9:totalReturn_undefined"


def test_light_revalidation_only_reruns_s2_s3_s6():
    thresholds = normalize_strategy_thresholds(None)
    assert light_revalidate(_PASS_IS, _PASS_OOS, thresholds) == ""
    assert light_revalidate({**_PASS_IS, "totalReturn": -0.1}, _PASS_OOS, thresholds) == "S2"
    assert light_revalidate({**_PASS_IS, "sharpe": 0.1}, _PASS_OOS, thresholds) == "S3"
    # 回撤 / 密度不参与轻量复评：即便把它们设成不可能过，复评也不该报 S4 / S7。
    assert light_revalidate({**_PASS_IS, "maxDrawdown": -0.99, "tradeRate": 0.9},
                            _PASS_OOS, thresholds) == ""


# ============================ 真回测下的 S6 与 S9 位置 ============================
def test_s6_is_not_decoration_under_a_real_two_regime_backtest():
    """IS 强趋势、OOS 反向震荡 ⇒ 必须被样本外这一关拦下（而不是一路放行）。"""
    from app.discovery.backtest_lane import ParamAssembler, run_split_backtest

    candles = two_regime_candles(1400, 600)
    ctx = lane_context(candles, split=1400)
    assembler = ParamAssembler("momentum_breakout",
                               {"surge_pct": 0.02, "breakout_window": 20, "vol_mult": 1.2,
                                "trail_pct": 0.02, "stop_loss_pct": 0.03,
                                "max_hold_bars": 48, "strength": 0.9})
    split = run_split_backtest(assembler, ctx)
    report = check_gates(split.is_metrics, split.oos_metrics,
                         thresholds=normalize_strategy_thresholds(None), trials=1, pool=[],
                         returns=[])
    assert report.reason != "", "两段分布完全相反却全数通过 ⇒ 样本外切分被写成了装饰"


def test_s9_runs_last_and_only_for_survivors(monkeypatch):
    """S9 是一次额外回测；未过 S1–S8 的候选**不得**触发第三段。"""
    calls: list = []
    original = backtest_lane.run_backtest
    monkeypatch.setattr(backtest_lane, "run_backtest",
                        lambda s, c, b, **kw: (calls.append(1) or original(s, c, b, **kw)))
    report = _check({"totalReturn": -1.0})
    assert report.reason == "S2"
    assert calls == [], "还没过 S2 就跑压力回测 ⇒ 成本白付"
    assert DEFAULT_STRATEGY_THRESHOLDS["costStressMult"] == 2.0
