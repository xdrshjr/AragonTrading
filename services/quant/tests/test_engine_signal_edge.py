"""信号前瞻收益引擎纯计算单测：8 信号检测 + 因果 + 右端截断 + 方向 + 基线 + t 值 (spec §6.1)。

合成 K 线 DataFrame，离线、确定性、不触网络。首行 ``import app.strategies`` 触发
``@register_strategy`` 填充 ``STRATEGY_REGISTRY``（否则候选策略用例形同虚设）。
"""
from __future__ import annotations

import math

import pytest

import app.strategies  # noqa: F401  填充 STRATEGY_REGISTRY（候选策略守护）
from app.engine.backtester import candles_to_df
from app.engine.signal_edge import (
    SIGNAL_LIBRARY,
    candidate_strategies_for_signal,
    detect_signal,
    forward_returns,
)
from app.strategies import STRATEGY_REGISTRY

_START = 1_700_000_000
_STEP = 3600


def _bar(i: int, o: float, h: float, l: float, c: float, vol: float = 1000.0) -> dict:
    return {"ts": _START + i * _STEP, "o": o, "h": h, "l": l, "c": c, "vol": vol}


def _df(rows: list[dict]):
    return candles_to_df(rows)


def _flat(n: int, close: float = 100.0, vol: float = 1000.0) -> list[dict]:
    return [_bar(i, close, close + 0.5, close - 0.5, close, vol) for i in range(n)]


def _ramp(values: list[float], vol: float = 1000.0):
    rows, prev = [], values[0]
    for i, c in enumerate(values):
        rows.append(_bar(i, prev, max(prev, c) + 0.5, min(prev, c) - 0.5, c, vol))
        prev = c
    return _df(rows)


def _wave_rows(n: int = 90, amp: float = 4.0, vol: float = 1000.0) -> list[dict]:
    rows, prev = [], 100.0
    for i in range(n):
        c = 100.0 + amp * math.sin(i / 4.0)
        rows.append(_bar(i, prev, max(prev, c) + 0.3, min(prev, c) - 0.3, c, vol))
        prev = c
    return rows


def _downtrend(n: int = 90):
    rows, prev = [], 200.0
    for i in range(n):
        c = 200.0 - 1.5 * i
        vol = 3000.0 if i % 5 == 0 else 1000.0
        rows.append(_bar(i, prev, max(prev, c) + 0.5, min(prev, c) - 0.5, c, vol))
        prev = c
    return _df(rows)


def _uptrend(n: int = 90):
    rows, prev = [], 100.0
    for i in range(n):
        c = 100.0 + 0.6 * i
        vol = 3000.0 if i % 5 == 0 else 1000.0
        rows.append(_bar(i, prev, max(prev, c) + 0.5, min(prev, c) - 0.5, c, vol))
        prev = c
    return _df(rows)


def _math_series():
    """flat 60 根 + 唯一一次放量突破 at 62，其后价格已知，便于手算前瞻收益。"""
    rows = _flat(70)
    rows[62] = _bar(62, 100.0, 110.5, 99.5, 110.0, 3000.0)
    rows[63] = _bar(63, 105.0, 106.5, 104.5, 106.0)
    rows[64] = _bar(64, 106.0, 107.5, 105.5, 107.0)
    rows[65] = _bar(65, 107.0, 108.5, 106.5, 108.0)
    for i in range(66, 70):
        rows[i] = _bar(i, 108.0, 108.5, 107.5, 108.0)
    return _df(rows)


# ============================ 信号检测 ============================
def test_each_signal_detects():
    bo = _flat(80)
    bo[70] = _bar(70, 100.0, 110.5, 99.5, 110.0, 3000.0)
    bd = _flat(80)
    bd[70] = _bar(70, 100.0, 100.5, 89.5, 90.0, 3000.0)
    bl = _wave_rows(90)
    bl[70] = _bar(70, 100.0, 100.5, 80.0, 99.0)
    bu = _wave_rows(90)
    bu[70] = _bar(70, 100.0, 120.0, 99.5, 101.0)
    cases = {
        "volume_breakout": _df(bo),
        "volume_breakdown": _df(bd),
        "rsi_oversold": _ramp([100.0 + i for i in range(45)]
                              + [145.0 - 3.0 * (i + 1) for i in range(35)]),
        "rsi_overbought": _ramp([200.0 - 2.0 * i for i in range(45)]
                                + [110.0 + 3.0 * (i + 1) for i in range(35)]),
        "bollinger_lower_touch": _df(bl),
        "bollinger_upper_touch": _df(bu),
        "ema_golden_cross": _ramp([200.0 - 2.0 * i for i in range(48)]
                                  + [104.0 + 3.0 * (i + 1) for i in range(42)]),
        "ema_death_cross": _ramp([100.0 + 2.0 * i for i in range(48)]
                                 + [196.0 - 3.0 * (i + 1) for i in range(42)]),
    }
    for signal, df in cases.items():
        assert detect_signal(df, signal).sum() >= 1, f"{signal} 未在预期序列触发"


def test_flat_series_yields_no_breakout():
    df = _df(_flat(80))
    assert detect_signal(df, "volume_breakout").sum() == 0
    assert detect_signal(df, "volume_breakdown").sum() == 0


def test_signal_edge_triggered_not_clustered():
    # 上行 45 根后陡降并持续低于 30：rsi 长时间 <30，但只在转入根触发一次（防聚簇）。
    df = _ramp([100.0 + i for i in range(45)] + [145.0 - 3.0 * (i + 1) for i in range(35)])
    series = detect_signal(df, "rsi_oversold")
    assert series.sum() == 1


def test_causality_no_lookahead():
    df = _downtrend(90)
    full = detect_signal(df, "volume_breakdown")
    for k in range(40, 90):
        partial = detect_signal(df.iloc[:k], "volume_breakdown")
        assert bool(partial.iloc[k - 1]) == bool(full.iloc[k - 1])


# ============================ 前瞻收益与统计 ============================
def test_forward_return_math():
    df = _math_series()
    next_open = forward_returns(df, "volume_breakout", horizons=(1, 3), entry_at="next_open")
    h1, h3 = next_open.horizons[0], next_open.horizons[1]
    assert next_open.occurrences == 1
    assert h1.count == 1 and h3.count == 1
    assert h1.meanRet == pytest.approx(106.0 / 105.0 - 1.0, abs=1e-4)   # 入 open[63]，出 close[63]
    assert h3.meanRet == pytest.approx(108.0 / 105.0 - 1.0, abs=1e-4)   # 入 open[63]，出 close[65]
    sig_close = forward_returns(df, "volume_breakout", horizons=(1, 3), entry_at="signal_close")
    assert sig_close.horizons[0].meanRet == pytest.approx(106.0 / 110.0 - 1.0, abs=1e-4)
    assert sig_close.horizons[1].meanRet == pytest.approx(108.0 / 110.0 - 1.0, abs=1e-4)


def test_right_edge_truncation():
    # 两次触发：bar50（窗口内）与 bar67（贴右端）；h=3 把 bar67 剔除 → count 随 h 减少。
    rows = _flat(70)
    rows[50] = _bar(50, 100.0, 110.5, 99.5, 110.0, 3000.0)
    for i in range(51, 67):
        rows[i] = _bar(i, 105.0, 105.5, 104.5, 105.0)
    rows[67] = _bar(67, 105.0, 120.5, 104.5, 120.0, 3000.0)
    for i in range(68, 70):
        rows[i] = _bar(i, 120.0, 120.5, 119.5, 120.0)
    result = forward_returns(_df(rows), "volume_breakout", horizons=(1, 3))
    assert result.occurrences == 2
    assert result.horizons[0].count == 2   # h=1：bar50、bar67 均合格
    assert result.horizons[1].count == 1   # h=3：bar67（67+3>69）被右端截断


def test_zero_count_horizon_safe():
    # 信号有触发，但大 horizon 被右端全部截断 → count==0 走空样本安全口径，不抛异常 / 不产 NaN。
    result = forward_returns(_math_series(), "volume_breakout", horizons=(1, 60))
    big = result.horizons[1]
    assert big.h == 60 and big.count == 0
    for field in ("winRate", "meanRet", "medianRet", "avgWin", "avgLoss",
                  "baselineRet", "edgeVsBaseline", "tStat", "netMeanRet"):
        assert getattr(big, field) == 0.0
    assert big.payoff is None
    assert not math.isnan(big.meanRet)


def test_direction_adjustment():
    # 下行序列：short 信号（放量跌破）方向调整后 winRate 高、均值为正（价格续跌即看对）。
    down = forward_returns(_downtrend(90), "volume_breakdown", horizons=(1, 3))
    assert down.occurrences >= 1
    assert down.horizons[0].winRate > 0.5 and down.horizons[0].meanRet > 0
    # 上行序列：long 信号（放量突破）方向调整后同样 winRate 高、均值为正。
    up = forward_returns(_uptrend(90), "volume_breakout", horizons=(1, 3))
    assert up.occurrences >= 1
    assert up.horizons[0].winRate > 0.5 and up.horizons[0].meanRet > 0


def test_baseline_and_edge():
    # 恒定百分比上行 + 周期放量：每根（触发与否）的 close-to-close 前瞻收益相同 → edge≈0（隔离漂移）。
    rows, prev = [], 100.0
    for i in range(90):
        c = 100.0 * (1.001 ** i)
        vol = 3000.0 if i % 3 == 0 else 1000.0
        rows.append(_bar(i, c, c + 0.05, c - 0.05, c, vol))
        prev = c
    result = forward_returns(_df(rows), "volume_breakout", horizons=(3,), entry_at="signal_close")
    assert result.occurrences >= 1
    assert result.horizons[0].edgeVsBaseline == pytest.approx(0.0, abs=1e-5)


def test_tstat_and_low_sample():
    single = forward_returns(_math_series(), "volume_breakout", horizons=(1,))
    assert single.horizons[0].count == 1
    assert single.horizons[0].stdRet == 0.0 and single.horizons[0].tStat == 0.0
    multi = forward_returns(_uptrend(120), "volume_breakout", horizons=(3,))
    stat = multi.horizons[0]
    assert stat.count >= 2 and stat.stdRet > 0
    expected = stat.meanRet / (stat.stdRet / math.sqrt(stat.count))
    assert stat.tStat == pytest.approx(expected, abs=0.02)


def test_zero_occurrence_is_ok():
    result = forward_returns(_df(_flat(80)), "volume_breakout", horizons=(1, 3, 6))
    assert result.occurrences == 0
    assert all(stat.count == 0 for stat in result.horizons)
    assert all(stat.payoff is None for stat in result.horizons)


def test_min_bars_guard():
    with pytest.raises(ValueError):
        forward_returns(_df(_flat(50)), "volume_breakout")


def test_unknown_signal_raises():
    with pytest.raises(ValueError):
        forward_returns(_df(_flat(80)), "not_a_signal")


def test_candidate_strategies_filtered():
    assert STRATEGY_REGISTRY, "STRATEGY_REGISTRY 为空（包导入未生效）"
    for signal in SIGNAL_LIBRARY:
        candidates = candidate_strategies_for_signal(signal)
        keys = [c["key"] for c in candidates]
        assert keys == list(dict.fromkeys(keys))            # 去重保序
        for cand in candidates:
            assert cand["key"] in STRATEGY_REGISTRY         # 只指向真实存在的 key
            assert cand["name"]                              # 补了 name
    multi = candidate_strategies_for_signal("rsi_oversold")
    assert [c["key"] for c in multi] == ["bollinger_reversion", "vwap_rsi_momentum"]
