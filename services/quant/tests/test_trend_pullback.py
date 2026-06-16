"""顺势回踩双策略单测 (spec §8)：因果性、门控、分级离场、实盘生命周期。"""
from __future__ import annotations

import pandas as pd

from app.engine.simulator import BrokerContext
from app.strategies import STRATEGY_REGISTRY
from app.strategies import _trend_structure as ts
from app.strategies.trend_pullback_long import TrendPullbackLong
from app.strategies.trend_pullback_short import TrendPullbackShort

# 便于触发的测试参数（小 EMA/pivot 窗、宽区域），逻辑与默认值同构。
TEST_PARAMS = {
    "htf_seconds": 3600, "htf_ema_fast": 5, "htf_ema_slow": 10, "atr_period": 5,
    "zone_atr_mult": 3.0, "fast_pivot_left": 2, "fast_pivot_right": 1,
    "slow_pivot_left": 3, "slow_pivot_right": 2, "vol_window": 5,
    "vol_spike_mult": 4.0, "stop_atr_mult": 1.5, "trail_atr_mult": 2.5,
    "trail_atr_mult_tight": 1.2, "stop_loss_pct": 0.05, "max_hold_bars": 500,
    "strength": 0.8,
}


def _flat_ctx() -> BrokerContext:
    return BrokerContext(position_side="flat", bars_in_position=0, entry_price=0,
                         equity=10000)


def _uptrend_pullbacks() -> list[dict]:
    """上涨周期 + 周期性回踩（每周期净抬升），用于触发回踩做多。"""
    rows: list[dict] = []
    t0 = 1_700_000_000
    k = 0

    def push(o, h, l, c, vol=1000.0):
        nonlocal k
        rows.append({"ts": t0 + k * 3600, "o": round(o, 2), "h": round(h, 2),
                     "l": round(l, 2), "c": round(c, 2), "vol": vol})
        k += 1

    base = 100.0
    for _ in range(16):
        for j in range(5):          # up-leg：5 根收阳爬升
            o = base + j
            push(o, o + 1.1, o - 0.3, o + 0.8)
        top = base + 5
        push(top, top + 0.2, top - 2.0, top - 1.8)          # 回踩红 1
        push(top - 1.8, top - 1.6, top - 3.2, top - 3.0)    # 回踩红 2（造摆动低点）
        lr = base + 1.9
        push(lr + 0.4, lr + 1.8, lr - 0.4, lr + 1.6)        # 收阳止跌（入场候选）
        base += 4.0
    return rows


def _downtrend_pullbacks() -> list[dict]:
    """下跌周期 + 周期性反弹（每周期净走低），用于触发反弹做空（上涨镜像）。"""
    rows: list[dict] = []
    t0 = 1_700_000_000
    k = 0

    def push(o, h, l, c, vol=1000.0):
        nonlocal k
        rows.append({"ts": t0 + k * 3600, "o": round(o, 2), "h": round(h, 2),
                     "l": round(l, 2), "c": round(c, 2), "vol": vol})
        k += 1

    base = 200.0
    for _ in range(16):
        for j in range(5):          # down-leg：5 根收阴下行
            o = base - j
            push(o, o + 0.3, o - 1.1, o - 0.8)
        bot = base - 5
        push(bot, bot + 2.0, bot - 0.2, bot + 1.8)          # 反弹绿 1
        push(bot + 1.8, bot + 3.2, bot + 1.6, bot + 3.0)    # 反弹绿 2（造摆动高点）
        hr = base - 1.9
        push(hr - 0.4, hr + 0.4, hr - 1.8, hr - 1.6)        # 收阴止涨（入场候选）
        base -= 4.0
    return rows


def _count_side(strat, rows: list[dict], side: str) -> int:
    df = strat.prepare(pd.DataFrame(rows), "1H")
    hits = 0
    for i in range(strat.warmup(), len(df)):
        sigs = strat.on_candle(df, i, _flat_ctx())
        if any(s.side == side for s in sigs):
            hits += 1
    return hits


# ---------------- 因果性（R4 平台硬规则，C4 红线）----------------

def test_pivot_confirmed_is_causal():
    low = pd.Series([10, 9, 8, 7, 5, 7, 8, 9, 10, 11, 12], dtype=float)
    conf = ts.pivot_low_confirmed(low, left=2, right=2)
    # 摆动低点中心在 index 4（值 5），只能在 index 6（中心 + right）后才暴露。
    assert pd.isna(conf.iloc[4])
    assert conf.iloc[5] != 5 or pd.isna(conf.iloc[5])
    assert conf.iloc[6] == 5
    # 改变 index k 之后的数据，不得影响 ≤ k 的确认值。
    low2 = low.copy()
    low2.iloc[8:] = [99.0, 99.0, 99.0]
    conf2 = ts.pivot_low_confirmed(low2, left=2, right=2)
    pd.testing.assert_series_equal(conf.iloc[:7], conf2.iloc[:7])


def test_htf_regime_causal():
    rows = _uptrend_pullbacks()
    df = pd.DataFrame(rows)
    r1 = ts.attach_htf_regime(df, 900, 3600, ema_fast=5, ema_slow=10,
                              atr_period=5, pivot_left=2, pivot_right=2)
    i = 90
    df2 = df.copy()
    for col in ("o", "h", "l", "c", "vol"):
        df2.loc[df2.index[i + 1:], col] = df2.loc[df2.index[i + 1:], col] * 1.7
    r2 = ts.attach_htf_regime(df2, 900, 3600, ema_fast=5, ema_slow=10,
                              atr_period=5, pivot_left=2, pivot_right=2)
    for col in ts.HTF_COLUMNS:
        pd.testing.assert_series_equal(
            r1[col].iloc[:i + 1], r2[col].iloc[:i + 1], check_names=False)


# ---------------- 门控 + 镜像 ----------------

def test_long_emits_long_on_pullback():
    assert _count_side(TrendPullbackLong(TEST_PARAMS), _uptrend_pullbacks(), "long") > 0
    # 下跌周期门控生效：绝不做多。
    assert _count_side(TrendPullbackLong(TEST_PARAMS), _downtrend_pullbacks(), "long") == 0


def test_short_mirror():
    assert _count_side(TrendPullbackShort(TEST_PARAMS), _downtrend_pullbacks(), "short") > 0
    # 上涨周期门控生效：绝不做空。
    assert _count_side(TrendPullbackShort(TEST_PARAMS), _uptrend_pullbacks(), "short") == 0


# ---------------- 分级离场 + 收紧解除 ----------------

def _exit_row(**vals) -> pd.Series:
    """构造一行含 on_candle 离场分支所需的结构列。"""
    cols = {
        "o": 0.0, "h": 0.0, "l": 0.0, "c": 0.0, "atr": 1.0,
        "fast_swing_low": 0.0, "slow_swing_low": 0.0, "fast_swing_high": 1e9,
        "slow_swing_high": 1e9, "htf_swing_low": -1e9, "htf_swing_high": 1e9,
        "htf_uptrend": True, "htf_downtrend": True,
    }
    cols.update(vals)
    return pd.Series(cols)


def test_staged_exit():
    strat = TrendPullbackLong(TEST_PARAMS)
    strat._peak = 100.0
    long_ctx = BrokerContext(position_side="long", bars_in_position=1,
                             entry_price=100, equity=10000)
    df_fast = _exit_row(c=99.0, h=99.2, fast_swing_low=99.5, slow_swing_low=98.0,
                        htf_swing_low=95.0).to_frame().T
    # 仅快结构破：收紧不清仓。
    assert strat.on_candle(df_fast, 0, long_ctx) == []
    assert strat._trail_armed is True
    # 慢结构也破：全平。
    df_slow = _exit_row(c=97.0, h=97.5, fast_swing_low=99.5, slow_swing_low=98.0,
                        htf_swing_low=95.0).to_frame().T
    out = strat.on_candle(df_slow, 0, long_ctx)
    assert any(s.side == "close" for s in out)


def test_trail_disarm_on_recovery():
    strat = TrendPullbackLong(TEST_PARAMS)
    strat._peak = 100.0
    long_ctx = BrokerContext(position_side="long", bars_in_position=1,
                             entry_price=100, equity=10000)
    # 1) 快结构破：收紧。
    strat.on_candle(_exit_row(c=99.0, h=99.2, fast_swing_low=99.5,
                              slow_swing_low=98.0, htf_swing_low=95.0).to_frame().T,
                    0, long_ctx)
    assert strat._trail_armed is True
    # 2) 结构站回(c>fast_swing_high)：解除收紧、不清仓。
    out = strat.on_candle(_exit_row(c=102.0, h=102.5, fast_swing_high=101.0,
                                    fast_swing_low=99.5, slow_swing_low=98.0,
                                    htf_swing_low=95.0).to_frame().T, 0, long_ctx)
    assert out == []
    assert strat._trail_armed is False
    # 3) 随后正常回踩到 101：宽跟踪(2.5)不触发；若仍收紧(1.2)则会被误平。
    out2 = strat.on_candle(_exit_row(c=101.0, h=101.0, fast_swing_high=101.0,
                                     fast_swing_low=99.0, slow_swing_low=98.0,
                                     htf_swing_low=95.0).to_frame().T, 0, long_ctx)
    assert out2 == []
    assert strat._trail_armed is False


# ---------------- 实盘生命周期（R1 关键回归）----------------

def test_state_survives_repeated_prepare():
    strat = TrendPullbackLong(TEST_PARAMS)
    # prepare() 不得写入任何持仓运行态（否则 paper/live 逐根 prepare 会清零）。
    strat._peak = 123.0
    strat._trail_armed = True
    df = strat.prepare(pd.DataFrame(_uptrend_pullbacks()), "1H")
    assert strat._peak == 123.0
    assert strat._trail_armed is True
    # 模拟 paper 路径：持多仓后逐根重复 prepare + on_candle，峰值应持续累积。
    strat._reset_state()
    strat._peak = 50.0
    long_ctx = BrokerContext(position_side="long", bars_in_position=1,
                             entry_price=50, equity=10000)
    rows = _uptrend_pullbacks()
    peaks = []
    for i in range(strat.warmup(), strat.warmup() + 8):
        sub = strat.prepare(pd.DataFrame(rows[:i + 1]), "1H")     # 逐根重算指标
        strat.on_candle(sub, i, long_ctx)
        peaks.append(strat._peak)
    assert peaks[-1] >= peaks[0]
    assert max(peaks) > 50.0  # 峰值确实被累积，而非被 prepare 重置


def test_warmup_is_pure():
    strat = TrendPullbackLong(TEST_PARAMS)
    w = strat.warmup()  # 未调用 prepare 即可得到正整数
    assert isinstance(w, int) and w > 0
    # 仅依赖 params：放大窗口 → warmup 变大。
    bigger = TrendPullbackLong({**TEST_PARAMS, "slow_pivot_left": 40})
    assert bigger.warmup() > w


def test_new_strategies_registered():
    for key in ("trend_pullback_long", "trend_pullback_short"):
        assert key in STRATEGY_REGISTRY
