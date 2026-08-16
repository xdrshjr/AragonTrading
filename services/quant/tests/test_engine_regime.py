"""体制引擎纯计算单测：三正交轴判定 + 因果性 + 子段合并 + 候选策略过滤 (spec §6.1)。

合成 K 线 DataFrame，离线、确定性、不触网络。首行 ``import app.strategies`` 触发
``@register_strategy`` 填充 ``STRATEGY_REGISTRY``（P1-3，否则 ``candidate_strategies``
用例形同虚设）。
"""
from __future__ import annotations

import math

import pytest

# P1-3：仅为填充 STRATEGY_REGISTRY 的副作用导入（故 F401 在此是预期的）。
import app.strategies  # noqa: F401
from app.engine.backtester import candles_to_df
from app.engine.regime import (
    RegimeParams,
    candidate_strategies,
    classify_regime,
    count_structural_runs,
    segment_regime,
)
from app.strategies import STRATEGY_REGISTRY

_START = 1_700_000_000
_STEP = 3600


def _bar(i: int, o: float, h: float, l: float, c: float, vol: float = 1000.0) -> dict:
    return {"ts": _START + i * _STEP, "o": o, "h": h, "l": l, "c": c, "vol": vol}


def _df(rows: list[dict]):
    return candles_to_df(rows)


def _trend(slope: float, n: int = 120, base: float = 100.0):
    rows, prev = [], base
    for i in range(n):
        c = base + slope * i
        rows.append(_bar(i, prev, max(prev, c) + abs(c) * 0.002, min(prev, c) - abs(c) * 0.002, c))
        prev = c
    return _df(rows)


def _range_df(n: int = 160, base: float = 100.0, amp: float = 5.0):
    rows, prev = [], base
    for i in range(n):
        c = base + amp * math.sin(i / 3.0)
        rows.append(_bar(i, prev, max(prev, c) + 0.3, min(prev, c) - 0.3, c))
        prev = c
    return _df(rows)


def _high_vol_df():
    """前 150 根极窄、后 30 根骤宽 → 末根 ATR% 高分位。"""
    rows = []
    for i in range(180):
        half = 6.0 if i >= 150 else 0.2
        rows.append(_bar(i, 100.0, 100.0 + half, 100.0 - half, 100.0))
    return _df(rows)


def _low_vol_df():
    """前 100 根宽、后 80 根收口 → 末根 ATR% 低分位。"""
    rows = []
    for i in range(180):
        half = 5.0 if i < 100 else 0.1
        rows.append(_bar(i, 100.0, 100.0 + half, 100.0 - half, 100.0))
    return _df(rows)


def _crash_df():
    """缓涨后 12 根放量瀑布下跌（急跌 ∧ 波动骤升 ∧ 低 RSI）。"""
    rows, prev = [], 100.0
    for i in range(128):
        c = 100.0 + 0.05 * i
        rows.append(_bar(i, prev, c + 0.3, c - 0.3, c))
        prev = c
    peak = prev
    for j in range(12):
        c = peak * (1 - 0.012 * (j + 1))
        rows.append(_bar(128 + j, prev, c + 4.0, c - 4.0, c, vol=4000.0))
        prev = c
    return _df(rows)


def _slow_decline_df():
    """宽幅震荡建立高 ATR 基线后，窄幅缓跌 ~10%（满足跌幅+低 RSI，但无波动骤升）。"""
    rows, prev = [], 150.0
    for i in range(120):
        c = 150.0 + 8.0 * math.sin(i / 2.0)
        rows.append(_bar(i, prev, c + 8.0, c - 8.0, c))
        prev = c
    base = prev
    for j in range(20):
        c = base * (1 - 0.008 * (j + 1))
        rows.append(_bar(120 + j, prev, c + 0.3, c - 0.3, c))
        prev = c
    return _df(rows)


def _breakout_df(vol_spike: bool = True):
    """窄区间后末根放量突破前高。"""
    rows, prev = [], 100.0
    for i in range(80):
        c = 100.0 + 1.0 * math.sin(i / 1.5)
        rows.append(_bar(i, prev, max(prev, c) + 0.2, min(prev, c) - 0.2, c))
        prev = c
    rows.append(_bar(80, prev, 106.2, prev - 0.2, 106.0, vol=3500.0 if vol_spike else 1000.0))
    return _df(rows)


def _alternating_df(block: int = 40, blocks: int = 5, slope: float = 0.8):
    rows, prev, price, gi = [], 100.0, 100.0, 0
    for b in range(blocks):
        step = slope if b % 2 == 0 else -slope
        for _ in range(block):
            price += step
            rows.append(_bar(gi, prev, max(prev, price) + 0.3, min(prev, price) - 0.3, price))
            prev = price
            gi += 1
    return _df(rows)


# ============================ 结构轴 ============================
def test_trend_up_detected():
    result = classify_regime(_trend(0.8), "1H")
    assert result.primary == "trend_up"
    assert result.scores["trend_up"] > result.scores["range"]


def test_trend_down_detected():
    assert classify_regime(_trend(-0.8, base=300.0), "1H").primary == "trend_down"


def test_range_detected():
    result = classify_regime(_range_df(), "1H")
    assert result.primary == "range"
    assert result.evidence.er < RegimeParams().er_trend_min


# ============================ 波动轴 ============================
def test_high_low_volatility_rank():
    assert classify_regime(_high_vol_df(), "1H").volatility == "high_volatility"
    assert classify_regime(_low_vol_df(), "1H").volatility == "low_volatility"


# ============================ 事件轴（合取防误报） ============================
def test_crash_requires_conjunction():
    assert classify_regime(_crash_df(), "1H").event == "crash"
    # 单纯阴跌（跌幅+低 RSI 满足，但无波动骤升）不判 crash。
    decline = classify_regime(_slow_decline_df(), "1H")
    assert decline.event != "crash"
    assert decline.evidence.fastDrop <= RegimeParams().crash_drop  # 跌幅条件确已满足
    assert decline.evidence.atrPctRank < RegimeParams().crash_vol_pct  # 唯独波动未骤升


def test_breakout_needs_volume_and_break():
    assert classify_regime(_breakout_df(vol_spike=True), "1H").event == "breakout"
    assert classify_regime(_breakout_df(vol_spike=False), "1H").event != "breakout"


# ============================ 因果性 ============================
def test_causality_no_lookahead():
    df = _trend(0.8, n=160)
    k = 120
    sub_primary = classify_regime(df.iloc[:k], "1H").primary
    target_ts = int(df["ts"].iloc[k - 1])
    segments = segment_regime(df, "1H")
    covering = [s for s in segments if s.startTs <= target_ts <= s.endTs]
    assert covering, "应有覆盖该根的子段"
    assert covering[0].primary == sub_primary


def test_min_bars_guard():
    with pytest.raises(ValueError):
        classify_regime(_trend(0.8, n=30), "1H")


# ============================ 子段合并 / 上限 ============================
def test_segment_merge_and_cap():
    df = _alternating_df(block=40, blocks=5)
    capped = segment_regime(df, "1H", max_segments=3, min_seg_bars=8)
    assert 1 <= len(capped) <= 3
    for prev_seg, next_seg in zip(capped, capped[1:]):
        assert prev_seg.primary != next_seg.primary  # 相邻同 primary 已合并

    def total_bars(segs):
        return sum(s.bars for s in segs)

    raw = segment_regime(df, "1H", max_segments=99, min_seg_bars=1)
    assert total_bars(capped) == total_bars(raw)  # 合并守恒，不丢根
    absorbed = segment_regime(df, "1H", max_segments=12, min_seg_bars=12)
    assert all(s.bars >= 12 for s in absorbed[1:])  # 非首段过短者已被吸收


def test_count_structural_runs_matches_raw():
    df = _alternating_df(block=40, blocks=4)
    raw = segment_regime(df, "1H", max_segments=99, min_seg_bars=1)
    assert count_structural_runs(df) == len(raw)


# ============================ 体制 → 候选策略 ============================
def test_candidate_strategies_filtered():
    assert len(STRATEGY_REGISTRY) >= 8  # P1-3 守护：注册表非空，否则本用例形同虚设
    out = candidate_strategies(["trend_up", "high_volatility", "trend_up"])
    keys = [c["key"] for c in out]
    assert keys == list(dict.fromkeys(keys))            # 去重保序
    assert all(c["key"] in STRATEGY_REGISTRY for c in out)
    assert all(c["name"] for c in out)
    assert candidate_strategies(["nonexistent_label"]) == []
