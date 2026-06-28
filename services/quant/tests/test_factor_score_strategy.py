"""因子组合得分策略测试：阈值产信号、空组合安静空仓、warmup、回测可跑通、快照归一化契约。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.engine.backtester import candles_to_df, run_backtest
from app.engine.factors import compose_score, member_names
from app.strategies import get_strategy_class
from app.strategies.factor_score_strategy import FactorScoreStrategy
from tests.conftest import make_synthetic_candles


_MEMBERS = [
    {"factorKey": "mom_roc", "params": {"win": 20}, "direction": "long", "weight": 0.5},
    {"factorKey": "rev_rsi", "params": {}, "direction": "short", "weight": 0.5},
]
_THRESHOLDS = {"enterLong": 0.2, "enterShort": 0.2, "exitBand": 0.05, "normWindow": 80, "squash": True}


def test_registered_with_factor_kind():
    cls = get_strategy_class("factor_score")
    assert cls is FactorScoreStrategy
    assert cls.meta.kind == "factor"


def test_warmup_accounts_for_norm_window():
    strat = FactorScoreStrategy({"members": _MEMBERS, "thresholds": {"normWindow": 200}})
    assert strat.warmup() >= 200


def test_backtest_runs_and_produces_equity():
    """用内联因子组合建 factor_score 策略，跑回测产出非空 equity。"""
    strat = FactorScoreStrategy({"members": _MEMBERS, "thresholds": _THRESHOLDS})
    candles = make_synthetic_candles(400, step=3600)
    result = run_backtest(strat, candles, "1H", initial_capital=10000,
                          taker_fee_bps=5, slippage_bps=2)
    assert len(result.equity_points) == 400
    assert "totalReturn" in result.metrics


def test_empty_members_quiet_flat():
    """空组合 → 0 成交、不抛错（安静空仓，回测期容错）。"""
    strat = FactorScoreStrategy({"members": [], "thresholds": {}})
    candles = make_synthetic_candles(300, step=3600)
    result = run_backtest(strat, candles, "1H", initial_capital=10000,
                          taker_fee_bps=5, slippage_bps=2)
    assert len(result.trades) == 0


def test_unknown_factor_quiet_flat_in_backtest():
    """未知 factorKey → 回测期安静空仓（边界校验由 API 负责，不在引擎抛错拖垮 compare）。"""
    strat = FactorScoreStrategy({
        "members": [{"factorKey": "no_such_factor", "direction": "long", "weight": 1.0}],
        "thresholds": _THRESHOLDS})
    candles = make_synthetic_candles(200, step=3600)
    result = run_backtest(strat, candles, "1H", initial_capital=10000,
                          taker_fee_bps=5, slippage_bps=2)
    assert len(result.trades) == 0


def test_new_snapshot_baked_weights_no_top_level_weights_scores():
    """新建快照（成员烘焙 weight、无顶层 weights、带 normStats）→ prepare 算出非全 NaN 得分（H3）。"""
    members = [{"factorKey": "mom_roc", "params": {"win": 20}, "direction": "long", "weight": 0.6},
               {"factorKey": "rev_rsi", "params": {}, "direction": "short", "weight": 0.4}]
    strat = FactorScoreStrategy({"members": members, "thresholds": {"normWindow": 80, "squash": True},
                                 "normStats": {"normWindow": 80, "clip": 4.0}})
    out = strat.prepare(candles_to_df(make_synthetic_candles(320, step=3600)), "1H")
    assert out["_score"].notna().any()


def test_old_snapshot_top_level_weights_still_scores():
    """R-P0-1 回归护栏：旧快照（成员 weight=0 + 顶层 weights、无 normStats）仍非全 NaN，能正常打分。

    删顶层 weights 读取路径会让旧策略全员 0 权重→得分恒 NaN→静默不交易；此用例钉死该回归绝不复发。
    """
    members = [{"factorKey": "mom_roc", "params": {"win": 20}, "direction": "long", "weight": 0.0},
               {"factorKey": "rev_rsi", "params": {}, "direction": "short", "weight": 0.0}]
    names = member_names(members)
    strat = FactorScoreStrategy({"members": members,
                                 "weights": {names[0]: 0.6, names[1]: 0.4},
                                 "thresholds": {"normWindow": 80, "squash": True}})
    out = strat.prepare(candles_to_df(make_synthetic_candles(320, step=3600)), "1H")
    assert out["_score"].notna().any()


def test_snapshot_uses_trained_norm_window_over_thresholds():
    """归一化契约（H1/H2）：快照带 normStats 时打分用训练窗口（60），而非 thresholds.normWindow（300）。"""
    members = [{"factorKey": "mom_roc", "params": {"win": 20}, "direction": "long", "weight": 1.0}]
    df = candles_to_df(make_synthetic_candles(360, step=3600))
    strat = FactorScoreStrategy({"members": members,
                                 "thresholds": {"normWindow": 300, "squash": False},
                                 "normStats": {"normWindow": 60, "clip": 4.0}})
    out = strat.prepare(df, "1H")
    ref60 = compose_score(df, members, None, norm_window=60, squash=False, bar="1H")
    merged = pd.concat([out["_score"], ref60], axis=1).dropna()
    assert len(merged) > 0
    assert np.allclose(merged.iloc[:, 0].to_numpy(), merged.iloc[:, 1].to_numpy())


def test_threshold_produces_long_and_short_signals():
    """构造得分穿越阈值 → 产出多 / 空 / 平信号（on_candle 逻辑直测）。"""
    import pandas as pd
    from app.engine.simulator import BrokerContext

    strat = FactorScoreStrategy({"members": _MEMBERS, "thresholds": _THRESHOLDS})
    strat._thresholds = _THRESHOLDS
    df = pd.DataFrame({"_score": [0.5, -0.5, 0.0]})
    flat = BrokerContext(position_side="flat", bars_in_position=0, entry_price=0.0, equity=10000.0)
    long_ctx = BrokerContext(position_side="long", bars_in_position=1, entry_price=100.0,
                             equity=10000.0)
    assert strat.on_candle(df, 0, flat)[0].side == "long"
    assert strat.on_candle(df, 1, flat)[0].side == "short"
    assert strat.on_candle(df, 2, long_ctx)[0].side == "close"
