"""大周期趋势带波段策略的共享结构工具 (spec trend-band-swing-strategy §2.3/§2.4)。

全部为纯函数、向量化、严格因果（无未来函数，平台硬规则 R4）：

- `band_features`：在**任意 OHLC 帧**（真实高周期 K 线或基础周期重采样桶）上算趋势方向 /
  布林带 / %B / 游程 / 回归幅度，**只产出数值列**——`merge_asof` 对未匹配基础行回填 NaN，
  而 `bool(float('nan')) is True` 会把「无数据」静默翻译成「趋势成立」，故布尔判定一律留到
  `on_candle` 内用 `pd.isna` 显式守卫后再做。
- `attach_resampled_features` / `attach_frame_features`：重采样兜底路径与真实帧路径，产出
  同名同前缀的列，使策略侧的读列代码只有一份。
- `live_pct_b`：**决策时**的实时 %B——带边界取自最近已收盘的高周期根、价格取当根基础收盘。
  高周期收盘 %B（`pct_b` 列）在两次收盘之间对所有基础根恒为常量，用它判「贴带」会让 15m/5m
  择时层不产生任何额外分辨率、也让「中轨开空 → 下轨平空」的往返跨不完（v2 P0-1），故 `zone`
  与目标带一律走本函数。
- `classify_bias` / `zone_of` / `entry_zone_allowed` / `target_pct_b`：决策表的纯函数形态。

输入帧须含 `ts,o,h,l,c` 列且按 ts 升序；所有输出列在历史不足处为 NaN，由调用方的 NaN 守卫兜住。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..engine.indicators import atr, bollinger, ema
from ..engine.mtf import causal_htf_merge

# `band_features` 产出的列（无前缀）；合并/映射后由调用方统一加 `tb_/mb_/cf_` 前缀。
BAND_FEATURE_COLUMNS = [
    "bar_ts",
    "ema_fast",
    "ema_slow",
    "atr",
    "slope_atr",
    "band_up",
    "band_mid",
    "band_low",
    "band_width",
    "pct_b",
    "trend_dir",
    "up_run",
    "down_run",
    "rebound_pct_b",
    "pullback_pct_b",
]


@dataclass(frozen=True)
class BandConfig:
    """单个周期的带 / 趋势特征参数（三个周期各实例化一份）。"""

    ema_fast: int
    ema_slow: int
    slope_lookback: int
    slope_min: float
    band_period: int
    band_std: float
    atr_period: int
    retrace_lookback: int


def _run_length(flag: pd.Series) -> pd.Series:
    """连续 True 的游程长度（False 处归零）；NaN 视为 False。

    显式 `astype(bool)` 再转 int8 不是洁癖：入参若为 float 序列，`~flag` 会抛 TypeError；
    bool dtype 直接进 `groupby().cumsum()` 在不同 pandas 版本行为不一致（v2 P2-2）。

    Args:
        flag: 逐根的布尔条件序列。

    Returns:
        与 `flag` 同索引的 float 序列（保持「只产数值列」硬规则）。
    """
    mask = flag.fillna(False).astype(bool)
    group = (~mask).cumsum()
    return mask.astype("int8").groupby(group).cumsum().astype(float)


def _trend_direction(feats: pd.DataFrame, slope_min: float) -> pd.Series:
    """由 EMA 关系 + ATR 归一化斜率派生 `+1 / -1 / 0`；任一输入 NaN ⇒ NaN。

    NaN 而不是 0：0 的语义是「震荡（已知）」，NaN 的语义是「不知道」，两者在方向层的
    处置完全不同（后者 fail-closed 到 `flat` 且连持仓期的失效检查都要跳过）。
    """
    fast, slow, slope = feats["ema_fast"], feats["ema_slow"], feats["slope_atr"]
    up = (fast > slow) & (slope >= float(slope_min))
    down = (fast < slow) & (slope <= -float(slope_min))
    out = pd.Series(0.0, index=feats.index).mask(up, 1.0).mask(down, -1.0)
    return out.mask(fast.isna() | slow.isna() | slope.isna(), float("nan"))


def band_features(frame: pd.DataFrame, cfg: BandConfig) -> pd.DataFrame:
    """在一个 OHLC 帧上算全部带 / 趋势特征（只产数值列，见模块 docstring）。

    Args:
        frame: 含 `ts,o,h,l,c` 的升序帧（真实高周期 K 线或重采样桶帧）。
        cfg: 该周期的特征参数。

    Returns:
        与 `frame` 同索引、含 `BAND_FEATURE_COLUMNS` 全部列的 DataFrame；历史不足处为 NaN。
    """
    close = frame["c"]
    out = pd.DataFrame(index=frame.index)
    # 该高周期根自身的 ts：合并后即「同一根真实趋势 K 线」的身份标识，频控桶直接取它，
    # 与交易所的对齐时区（OKX 12H/1D 是港时开盘）彻底解耦（v2 P1-1）。
    out["bar_ts"] = frame["ts"].astype("int64").astype(float)
    out["ema_fast"] = ema(close, int(cfg.ema_fast))
    out["ema_slow"] = ema(close, int(cfg.ema_slow))
    out["atr"] = atr(frame, int(cfg.atr_period))
    slope = out["ema_slow"] - out["ema_slow"].shift(int(cfg.slope_lookback))
    # 用 ATR 归一化：同一个阈值才能跨标的、跨周期、跨波动率区间可比。
    out["slope_atr"] = slope / out["atr"].where(out["atr"] > 0)
    upper, mid, lower = bollinger(close, int(cfg.band_period), float(cfg.band_std))
    out["band_up"] = upper
    out["band_mid"] = mid
    out["band_low"] = lower
    out["band_width"] = (upper - lower) / mid.where(mid > 0)
    span = upper - lower
    out["pct_b"] = (close - lower) / span.where(span > 0)
    out["trend_dir"] = _trend_direction(out, float(cfg.slope_min))
    out["up_run"] = _run_length(close > close.shift(1))
    out["down_run"] = _run_length(close < close.shift(1))
    look = int(cfg.retrace_lookback)
    pct_b = out["pct_b"]
    out["rebound_pct_b"] = pct_b - pct_b.rolling(look, min_periods=look).min()
    out["pullback_pct_b"] = pct_b.rolling(look, min_periods=look).max() - pct_b
    return out


def _bucket_frame(df: pd.DataFrame, tf_seconds: int) -> tuple[pd.DataFrame, pd.Series]:
    """按 `ts // tf_seconds` 聚合出桶级 OHLC 帧，并返回每个基础行所属的桶 id。"""
    buckets = (df["ts"] // int(tf_seconds)).astype("int64")
    grouped = df.groupby(buckets, sort=True)
    bucket_ids = grouped["c"].last().index.to_numpy().astype("int64")
    frame = pd.DataFrame({
        "ts": bucket_ids * int(tf_seconds),
        "o": grouped["o"].first().to_numpy(),
        "h": grouped["h"].max().to_numpy(),
        "l": grouped["l"].min().to_numpy(),
        "c": grouped["c"].last().to_numpy(),
    }, index=bucket_ids)
    return frame, buckets


def attach_resampled_features(
    df: pd.DataFrame, base_seconds: int, tf_seconds: int, cfg: BandConfig, *, prefix: str,
) -> pd.DataFrame:
    """重采样兜底：取不到真实高周期 K 线时，从基础帧因果重采样出同名前缀列。

    `base_seconds >= tf_seconds`（例如直接在 1D 上跑 12H 趋势）退化为基础周期自身；否则
    分桶聚合后在桶级序列上算特征，再 `shift(1)` 桶映射回基础行——当前（未收盘）桶只能看到
    已收盘的上一个桶，杜绝桶内未来基础根。

    ⚠️ 兜底桶与交易所不同相位：OKX 的 `12H`/`1D` 是港时（UTC+8）开盘价 K 线，而这里是 UTC
    整除分桶，两者差 4 小时。这是**已知降级**而非缺陷：兜底只在取不到真实帧时生效，那时策略
    本就靠开仓 fail-closed 少开仓，不构成风险敞口（v2 R-7）。

    Returns:
        与 `df` 同索引、列名带 `prefix` 的 DataFrame。
    """
    if int(base_seconds) >= int(tf_seconds):
        return band_features(df, cfg).add_prefix(prefix)
    frame, buckets = _bucket_frame(df, int(tf_seconds))
    feats = band_features(frame, cfg)
    mapped = feats.shift(1).reindex(buckets.to_numpy())
    mapped.index = df.index
    return mapped.add_prefix(prefix)


def attach_frame_features(
    base_df: pd.DataFrame, htf_df: pd.DataFrame, htf_seconds: int, cfg: BandConfig, *, prefix: str,
) -> pd.DataFrame:
    """真实帧路径：按「高周期收盘时间」因果并入基础行（`causal_htf_merge` 单一真源）。"""
    return causal_htf_merge(base_df, htf_df, int(htf_seconds),
                            compute=lambda frame: band_features(frame, cfg), prefix=prefix)


def live_pct_b(close: float, band_low: float, band_up: float) -> float:
    """当根基础收盘价在【最近已收盘高周期带】中的相对位置；带退化（分母 ≤ 0）返回 NaN。

    带边界来自已收盘的高周期根（12H 每 12 小时才更新），价格是当根基础收盘（5m 每 5 分钟
    更新）。这样「贴带」判定拿到的是基础周期的分辨率，而带本身仍由高周期结构定义——小周期
    只决定「什么时候贴上去了」，从不重新定义带（v2 P0-1）。

    严格因果：`c` 是当根收盘后才可读的量，而信号在次根开盘才成交，与既有全部策略读 `row["c"]`
    的口径完全一致。
    """
    span = float(band_up) - float(band_low)
    if not span > 0:
        return float("nan")
    return (float(close) - float(band_low)) / span


def classify_bias(
    trend_dir: float, macro_dir: float, *, allow_partial: bool, partial_scale: float,
) -> tuple[str, float]:
    """方向层：由交易趋势周期与宏观周期的两票得出 `(bias, conviction)`。

    任一输入为 NaN ⇒ `("flat", 0.0)`（开仓 fail-closed，铁律 I-2）；两票冲突同样 `flat`
    ——「不能违背总趋势」在没有总趋势时的正确解读是不交易，而不是挑一票（铁律 I-3a）。

    Args:
        trend_dir: 交易趋势周期（默认 12H）的 `trend_dir`，`+1/-1/0/NaN`。
        macro_dir: 宏观周期（默认 1D）的 `trend_dir`。
        allow_partial: 是否允许「一边成立、另一边震荡」构成降权偏置。
        partial_scale: 上者的仓位折扣（即 conviction）。

    Returns:
        `(bias, conviction)`，`bias ∈ {"long", "short", "flat"}`，`conviction ∈ [0, 1]`。
    """
    if pd.isna(trend_dir) or pd.isna(macro_dir):
        return "flat", 0.0
    trend = int(round(float(trend_dir)))
    macro = int(round(float(macro_dir)))
    if trend > 0 and macro > 0:
        return "long", 1.0
    if trend < 0 and macro < 0:
        return "short", 1.0
    if trend * macro < 0 or (trend == 0 and macro == 0):
        return "flat", 0.0
    if not allow_partial:
        return "flat", 0.0
    side = "long" if max(trend, macro) > 0 else "short"
    return side, float(partial_scale)


def zone_of(pct_b: float, tol: float) -> str:
    """位置层：实时 %B 落在哪条带的容差内。

    外侧两锚是**单边判据**：`pct_b <= tol → lower`、`pct_b >= 1 - tol → upper`。若写成
    双边（`|pct_b - 0| <= tol`），破轨行情（%B ∉ [0,1]）反而落回 `none` 静默不开仓，而破轨
    恰恰是趋势加速段、也正是 `allow_edge_entry` 想覆盖那类位置的极端形态——门禁在最该表态时
    沉默是空洞不是保守（v2 P1-2）。

    `tol` 的上界锁 0.24：`tol = 0.25` 时三锚区首尾相接，「贴带」退化成「只要在带内」，门禁
    形同虚设。`tol <= 0.24 ⇒ tol < 0.5 - tol 且 0.5 + tol < 1 - tol`，三区两两互斥。
    """
    if pct_b is None or pd.isna(pct_b):
        return "none"
    value = float(pct_b)
    if value <= float(tol):
        return "lower"
    if value >= 1.0 - float(tol):
        return "upper"
    if abs(value - 0.5) <= float(tol):
        return "mid"
    return "none"


def entry_zone_allowed(bias: str, zone: str, *, allow_edge_entry: bool) -> bool:
    """开仓决策表：「方向 × 贴哪条线」是否构成一次顺势回归进场。

    顺势回归（空势贴上轨/中轨、多势贴下轨/中轨）才开；追单（空势贴下轨、多势贴上轨）一律否，
    逆势更是从这里就无从产生——本函数永远不会为 `bias` 的反方向返回 True（铁律 I-3a）。
    """
    if bias == "long":
        if zone == "mid":
            return True
        return zone == "lower" and bool(allow_edge_entry)
    if bias == "short":
        if zone == "mid":
            return True
        return zone == "upper" and bool(allow_edge_entry)
    return False


def target_pct_b(bias: str, entry_zone: str, edge: float) -> float:
    """离场目标带：从边轨进场打到中轨落袋，从中轨进场打到对侧轨。

    Returns:
        目标 %B；`(bias, entry_zone)` 不成对（如空仓期的空串）返回 NaN，由调用方按
        「缺数据只跳过该项检查」处理（离场 fail-open，铁律 I-2）。
    """
    if bias == "short":
        if entry_zone == "upper":
            return 0.5
        if entry_zone == "mid":
            return float(edge)
    elif bias == "long":
        if entry_zone == "lower":
            return 0.5
        if entry_zone == "mid":
            return 1.0 - float(edge)
    return float("nan")
