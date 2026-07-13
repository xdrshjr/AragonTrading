"""多周期核心单测 (MTF §7)：因果对齐无未来函数、秒↔bar 映射、缺数据 NaN、P1-1 布尔派生。"""
from __future__ import annotations

import pandas as pd
import pytest

from app.engine.mtf import bar_to_seconds, causal_htf_merge, seconds_to_bar
from app.okx.types import BAR_TO_WS_CHANNEL
from app.strategies import _trend_structure as ts

_T0 = 1_700_000_000


def _ohlc(ts_val: int, close: float) -> dict:
    return {"ts": ts_val, "o": close, "h": close + 1.0, "l": close - 1.0,
            "c": close, "vol": 100.0}


def _base_5m(n: int) -> pd.DataFrame:
    df = pd.DataFrame([_ohlc(_T0 + i * 300, 50.0) for i in range(n)])
    return df.reset_index(drop=True)


def _htf_1h(closes: list[float]) -> pd.DataFrame:
    df = pd.DataFrame([_ohlc(_T0 + i * 3600, c) for i, c in enumerate(closes)])
    return df.reset_index(drop=True)


def _passthrough_close(htf_df: pd.DataFrame) -> pd.DataFrame:
    """最简 compute：把高周期收盘价作为单一数值特征列（验证对齐本身）。"""
    return pd.DataFrame({"val": htf_df["c"].to_numpy()})


# ---------------- T2：秒 ↔ bar 双向映射 ----------------

def test_seconds_to_bar_roundtrip_and_nonstandard():
    assert bar_to_seconds("1H") == 3600
    assert seconds_to_bar(3600) == "1H"
    assert seconds_to_bar(300) == "5m"
    assert seconds_to_bar(7200) is None        # 非标准秒数 → None（调用方回退重采样）
    with pytest.raises(ValueError):
        bar_to_seconds("7m")                   # 未知 bar 一次性抛错（边界校验）


def test_12h_is_rest_only_not_an_execution_ws_bar():
    assert bar_to_seconds("12H") == 43_200
    assert seconds_to_bar(43_200) == "12H"
    assert "12H" not in BAR_TO_WS_CHANNEL


# ---------------- T1：因果对齐，无未来函数（核心红线 R4）----------------

def test_causal_merge_only_sees_closed_htf():
    base = _base_5m(80)                          # T0 .. T0+23700
    htf = _htf_1h([100.0, 101.0, 102.0, 103.0, 104.0])  # close at T0+3600 .. T0+18000
    out = causal_htf_merge(base, htf, 3600, compute=_passthrough_close, prefix="htf_")
    col = out["htf_val"]
    # 首个 1H 收盘(T0+3600)之前的基础行无可见高周期 → NaN。
    assert pd.isna(col.iloc[0])                  # ts=T0
    assert pd.isna(col.iloc[11])                 # ts=T0+3300 < 首个收盘
    # ts==首个收盘时间：恰好看到已收盘的第 0 根 1H。
    assert col.iloc[12] == 100.0                 # ts=T0+3600
    assert col.iloc[23] == 100.0                 # ts=T0+6900，仍在第 0 根覆盖内
    assert col.iloc[24] == 101.0                 # ts=T0+7200，看到第 1 根


def test_causal_merge_future_bar_change_does_not_leak_backward():
    base = _base_5m(80)
    htf = _htf_1h([100.0, 101.0, 102.0, 103.0, 104.0])
    before = causal_htf_merge(base, htf, 3600, compute=_passthrough_close, prefix="htf_")
    # 改动第 3 根 1H（收盘 T0+14400）的收盘价，只应影响 ts>=T0+14400 的基础行。
    htf2 = htf.copy()
    htf2.loc[3, "c"] = 999.0
    after = causal_htf_merge(base, htf2, 3600, compute=_passthrough_close, prefix="htf_")
    boundary = (14400) // 300                     # = idx 48（ts=T0+14400）
    pd.testing.assert_series_equal(
        before["htf_val"].iloc[:boundary], after["htf_val"].iloc[:boundary])
    assert after["htf_val"].iloc[boundary] == 999.0
    assert before["htf_val"].iloc[boundary] != 999.0


# ---------------- T3：高周期历史不足 → NaN（不抛异常）----------------

def test_unmatched_rows_are_nan_not_error():
    base = _base_5m(20)                           # 全部落在首个 1H 收盘之前一部分
    htf = _htf_1h([100.0, 101.0, 102.0])
    out = causal_htf_merge(base, htf, 3600, compute=_passthrough_close, prefix="htf_")
    assert out["htf_val"].iloc[:12].isna().all()  # ts<T0+3600 的早段全 NaN
    assert out["htf_val"].iloc[12:].notna().all()


def test_merge_requires_unique_monotonic_index():
    base = _base_5m(20)
    base.index = [0] * len(base)                  # 破坏唯一性（P2-4 不变量）
    with pytest.raises(ValueError):
        causal_htf_merge(base, _htf_1h([1.0, 2.0]), 3600,
                         compute=_passthrough_close, prefix="htf_")


# ---------------- T-bool（P1-1）：布尔趋势列在 merge 之后派生，NaN→False ----------------

def test_bool_flags_derived_after_merge_are_false_not_nan():
    base = _base_5m(80)
    # 上行的真实 1H 帧（递增收盘），用 regime_from_htf_frame 产数值列、合并后派生布尔列。
    htf = _htf_1h([100.0 + i for i in range(6)])

    def _compute(h: pd.DataFrame) -> pd.DataFrame:
        return ts.regime_from_htf_frame(h, ema_fast=2, ema_slow=4, atr_period=2,
                                        pivot_left=1, pivot_right=1)

    merged = causal_htf_merge(base, htf, 3600, compute=_compute, prefix="htf_")
    df = base.copy()
    for c in merged.columns:
        df[c] = merged[c]
    ts._attach_trend_flags(df)
    # 早段未匹配行：数值列 NaN，但派生布尔列必须是 False（绝非 NaN / True）。
    assert pd.isna(df["htf_ema_fast"].iloc[0])
    assert df["htf_uptrend"].iloc[0] == False     # noqa: E712 —— 显式断言布尔值而非真值性
    assert df["htf_downtrend"].iloc[0] == False    # noqa: E712
    assert df["htf_uptrend"].dtype == bool
    # 后段有数据且上行：应出现 uptrend=True。
    assert bool(df["htf_uptrend"].iloc[-1]) is True
