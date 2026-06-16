"""顺势回踩双策略的共享结构工具 (spec §3.2)。

全部为纯函数、向量化、严格因果（无未来函数，平台硬规则 R4）：

- `attach_htf_regime`：从基础周期 df 因果重采样出高周期(HTF)趋势/区域结构；
  在 HTF 桶级序列上算 EMA / ATR / 摆动点后整体 `shift(1)` 桶，再按桶映射回基础行，
  保证基础行只能看到「已收盘的上一个 HTF 桶」。
- `pivot_low_confirmed` / `pivot_high_confirmed`：确认型分形摆动点，
  其值只在中心点右移 `right` 根后才暴露（`shift(right)`），杜绝引用未来根。
- `attach_volume_flags`：缩量回踩 / 放量砸穿标志。

输入 df 须含 `ts,o,h,l,c,vol` 列且按 ts 升序；所有输出列在历史不足时为 NaN，
由调用方 `on_candle` 的 NaN 守卫兜住。
"""
from __future__ import annotations

import pandas as pd

from ..engine.indicators import atr, ema

# attach_htf_regime 返回的列（含派生趋势布尔列）。
HTF_COLUMNS = [
    "htf_ema_fast",
    "htf_ema_slow",
    "htf_slope",
    "htf_atr",
    "htf_high",
    "htf_low",
    "htf_swing_low",
    "htf_swing_high",
    "htf_uptrend",
    "htf_downtrend",
]


def pivot_low_confirmed(low: pd.Series, left: int, right: int) -> pd.Series:
    """确认型摆动低点：返回「截至当前可用的最近一个已确认摆动低点」。

    分形定义：`low[k]` 是窗口 `[k-left, k+right]` 的最小值即为摆动低点；
    该点只有在第 `k+right` 根（右窗收齐）才被确认可用，故先 `where` 取值、
    再 `shift(right)` 延迟暴露、最后 `ffill`。这样读取第 i 处的值只依赖 ≤ i 的数据。

    Args:
        low: 低价序列。
        left: 中心点左侧窗口根数。
        right: 中心点右侧窗口根数（确认延迟）。

    Returns:
        与 `low` 同索引的序列；历史不足处为 NaN。
    """
    window = int(left) + int(right) + 1
    roll_min = low.rolling(window=window, min_periods=window).min()
    # roll_min 在位置 p 覆盖 [p-window+1, p]；中心 k 的窗口右端在 k+right。
    centered_min = roll_min.shift(-int(right))
    is_pivot = low.eq(centered_min)
    pivot_val = low.where(is_pivot)
    return pivot_val.shift(int(right)).ffill()


def pivot_high_confirmed(high: pd.Series, left: int, right: int) -> pd.Series:
    """确认型摆动高点（`pivot_low_confirmed` 的镜像）。"""
    window = int(left) + int(right) + 1
    roll_max = high.rolling(window=window, min_periods=window).max()
    centered_max = roll_max.shift(-int(right))
    is_pivot = high.eq(centered_max)
    pivot_val = high.where(is_pivot)
    return pivot_val.shift(int(right)).ffill()


def attach_volume_flags(
    vol: pd.Series, window: int, contract_ratio: float, spike_mult: float
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """量能标志：均量、缩量回踩、放量砸穿/突破。

    均量用 `vol.shift(1)`（不含当根，避免用当根量判断当根）。与 NaN 比较得 False，
    即历史不足时默认「非缩量、非放量」，由调用方均量 NaN 守卫进一步兜底。

    Returns:
        `(vol_mean, vol_contracting, vol_spike)`。
    """
    vol_mean = vol.shift(1).rolling(int(window), min_periods=int(window)).mean()
    vol_contracting = vol.le(float(contract_ratio) * vol_mean)
    vol_spike = vol.ge(float(spike_mult) * vol_mean)
    return vol_mean, vol_contracting, vol_spike


def attach_htf_regime(
    df: pd.DataFrame,
    base_seconds: int,
    htf_seconds: int,
    *,
    ema_fast: int,
    ema_slow: int,
    atr_period: int,
    pivot_left: int,
    pivot_right: int,
) -> pd.DataFrame:
    """因果重采样出高周期趋势 / 波动率 / 摆动点结构 (spec §3.2)。

    `base_seconds >= htf_seconds`（在 1H/4H/1D 上跑）时退化为基础周期本身；
    否则按 `ts // htf_seconds` 分桶聚合 OHLC，在桶级序列上算指标后 `shift(1)` 桶
    再因果映射回基础行。

    Returns:
        与 `df` 同索引、含 `HTF_COLUMNS` 全部列的 DataFrame。
    """
    if int(base_seconds) >= int(htf_seconds):
        return _regime_degenerate(df, ema_fast, ema_slow, atr_period,
                                  pivot_left, pivot_right)
    return _regime_resampled(df, int(htf_seconds), ema_fast, ema_slow,
                             atr_period, pivot_left, pivot_right)


def _regime_degenerate(
    df: pd.DataFrame, ema_fast: int, ema_slow: int, atr_period: int,
    pivot_left: int, pivot_right: int,
) -> pd.DataFrame:
    """退化分支：HTF 即基础周期（桶级 high=h、low=l、close=c），无需分桶或位移。

    各列在第 i 行只依赖 ≤ i 的数据（EMA/ATR 因果、摆动点自带 `shift(right)` 确认）。
    """
    close = df["c"]
    out = pd.DataFrame(index=df.index)
    out["htf_ema_fast"] = ema(close, int(ema_fast))
    out["htf_ema_slow"] = ema(close, int(ema_slow))
    out["htf_slope"] = out["htf_ema_slow"].diff()
    out["htf_atr"] = atr(df, int(atr_period))
    out["htf_high"] = df["h"]
    out["htf_low"] = df["l"]
    out["htf_swing_low"] = pivot_low_confirmed(df["l"], pivot_left, pivot_right)
    out["htf_swing_high"] = pivot_high_confirmed(df["h"], pivot_left, pivot_right)
    _attach_trend_flags(out)
    return out


def _regime_resampled(
    df: pd.DataFrame, htf_seconds: int, ema_fast: int, ema_slow: int,
    atr_period: int, pivot_left: int, pivot_right: int,
) -> pd.DataFrame:
    """重采样分支：桶级聚合 → 桶级指标 → `shift(1)` 桶 → 按桶因果映射回基础行。"""
    buckets = (df["ts"] // int(htf_seconds)).astype("int64")
    grouped = df.groupby(buckets, sort=True)
    htf_h = grouped["h"].max()
    htf_l = grouped["l"].min()
    htf_c = grouped["c"].last()
    htf_ohlc = pd.DataFrame({"h": htf_h, "l": htf_l, "c": htf_c})

    per_bucket = pd.DataFrame(index=htf_c.index)
    per_bucket["htf_ema_fast"] = ema(htf_c, int(ema_fast))
    per_bucket["htf_ema_slow"] = ema(htf_c, int(ema_slow))
    per_bucket["htf_slope"] = per_bucket["htf_ema_slow"].diff()
    per_bucket["htf_atr"] = atr(htf_ohlc, int(atr_period))
    per_bucket["htf_high"] = htf_h
    per_bucket["htf_low"] = htf_l
    per_bucket["htf_swing_low"] = pivot_low_confirmed(htf_l, pivot_left, pivot_right)
    per_bucket["htf_swing_high"] = pivot_high_confirmed(htf_h, pivot_left, pivot_right)

    # shift(1) 桶：当前(未收盘)桶只能看到已收盘的上一个桶，杜绝桶内未来基础根。
    shifted = per_bucket.shift(1)
    mapped = shifted.reindex(buckets.to_numpy())
    mapped.index = df.index
    _attach_trend_flags(mapped)
    return mapped


def _attach_trend_flags(frame: pd.DataFrame) -> None:
    """就地派生 `htf_uptrend / htf_downtrend`（与 NaN 比较得 False，永不为 NaN）。"""
    frame["htf_uptrend"] = (
        (frame["htf_ema_fast"] > frame["htf_ema_slow"]) & (frame["htf_slope"] > 0)
    )
    frame["htf_downtrend"] = (
        (frame["htf_ema_fast"] < frame["htf_ema_slow"]) & (frame["htf_slope"] < 0)
    )
