"""多周期（MTF, Multi-Timeframe）核心 (spec multi-timeframe-strategy-signals §3.2)。

纯计算、无 IO（与 `backtester.py` 同档）：所有 OKX 取数留在 `api/strategies.py`
与 `paper/session.py` 边界。提供：

- `bar_to_seconds` / `seconds_to_bar`：标准 OKX bar 与秒数的双向映射（单一真源）。
- `causal_htf_merge`：把高周期指标**因果**并入基础周期行（无未来函数，平台硬规则 R4）。

因果对齐原理：一根「开盘时间 = T」的高周期 K 线覆盖区间 `[T, T+htf_seconds)`，
在 `T+htf_seconds` 时刻才收盘。基础周期在时刻 `t` 只能引用「收盘时间 ≤ t」的高周期
K 线，故对齐键取「高周期收盘时间 = ts + htf_seconds」，用 `merge_asof(backward)` 即可，
天然杜绝引用未收盘的当根高周期（与既有 `_trend_structure` 的 `shift(1)` 桶语义等价，
但数据源换成真实高周期 K 线、历史不再受基础窗口限制）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from ..okx.types import BAR_SECONDS

# 秒数 → 标准 OKX bar（仅这些可向 OKX 取真实 K 线）
_SECONDS_TO_BAR = {v: k for k, v in BAR_SECONDS.items()}


def bar_to_seconds(bar: str) -> int:
    """标准 bar → 秒；未知 bar 抛 ValueError（边界一次性校验）。"""
    if bar not in BAR_SECONDS:
        raise ValueError(f"未知 bar: {bar!r}")
    return BAR_SECONDS[bar]


def seconds_to_bar(seconds: int) -> str | None:
    """秒 → 标准 OKX bar；非标准秒数返回 None（调用方据此回退重采样）。"""
    return _SECONDS_TO_BAR.get(int(seconds))


@dataclass
class MTFBundle:
    """一次驱动所需的多周期数据包：基础 df + 各高周期真实 K 线 df。"""

    base_df: pd.DataFrame
    bar: str
    base_seconds: int
    frames: dict[str, pd.DataFrame]   # htf_bar -> 升序 ts 的 OHLC df


def causal_htf_merge(
    base_df: pd.DataFrame,
    htf_df: pd.DataFrame,
    htf_seconds: int,
    *,
    compute: Callable[[pd.DataFrame], pd.DataFrame],
    prefix: str,
) -> pd.DataFrame:
    """把高周期指标因果并入基础行，返回与 base_df 同索引、带 prefix 的列。

    Args:
        base_df: 基础周期 DataFrame，须含 `ts` 列且索引为唯一、单调递增的 RangeIndex
            （由 `candles_to_df` 的 `reset_index(drop=True)` 保证，P2-4 不变量）。
        htf_df: 高周期真实 K 线（列 `ts,o,h,l,c,vol`，升序）。
        htf_seconds: 高周期秒数；对齐键 = `ts + htf_seconds`（高周期收盘时间）。
        compute: 在高周期序列上算指标的纯函数，须自身因果（EMA/ATR/确认型摆动点）。
            **只产出数值列、不得产出布尔趋势列**（P1-1：`merge_asof` 对未匹配基础行回填
            NaN，而 `bool(float('nan')) is True` 会静默反转门控；布尔列须在合并后由数值列派生）。
            其列名应为「无 prefix」的特征名（如 `ema_fast`），最终列名由本函数加上 `prefix`。
        prefix: 输出列名前缀（如 `htf_` 或 `htf_1H_`）。

    Returns:
        与 `base_df` 同索引的 DataFrame；历史不足/未匹配处为 NaN（由调用方 NaN 守卫兜住）。
    """
    if not (base_df.index.is_unique and base_df.index.is_monotonic_increasing):
        raise ValueError("causal_htf_merge 要求 base_df 索引唯一且单调递增（RangeIndex）")

    feats = compute(htf_df).reset_index(drop=True)
    close_ts = (htf_df["ts"].astype("int64") + int(htf_seconds)).reset_index(drop=True)
    right = pd.concat([close_ts.rename("_close_ts"), feats], axis=1).sort_values("_close_ts")
    left = base_df[["ts"]].reset_index().sort_values("ts")   # 原索引保留于 'index' 列
    merged = pd.merge_asof(
        left, right, left_on="ts", right_on="_close_ts", direction="backward",
    )
    merged = merged.set_index("index").reindex(base_df.index)  # 还原基础行顺序
    out = merged.drop(columns=["ts", "_close_ts"])
    return out.add_prefix(prefix)
