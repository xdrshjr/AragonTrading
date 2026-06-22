"""信号前瞻收益引擎 —— 纯计算、严格因果、edge-triggered (spec §2.3 / §2.4)。

回答「某具名信号（放量突破 / RSI 超卖 / 触及布林下轨等）出现后，未来 N 根 K 线的
前瞻收益分布、胜率、显著性如何，是否跑赢同期无条件基线」——量化 alpha 研究里的事件研究
（event study）。不跑回测、不撮合、不计风控，只做确定性可复现的统计。

判据仅用 ``engine.indicators`` 现有纯函数（EMA / RSI / Bollinger），全部相对量纲、严格因果
（检测只用 ≤ i 数据）。状态型信号一律 edge-trigger（只取状态转入根）以减少前瞻窗口重叠、
避免 t 值高估。不触网络 / DB / 磁盘 IO；经**包导入** ``from ..strategies import
STRATEGY_REGISTRY`` 触发 ``@register_strategy`` 副作用（同 ``engine/regime.py``），据此把
「信号 → 候选策略」映射过滤到真实存在的 key（R7）。
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from ..strategies import STRATEGY_REGISTRY
from .indicators import bollinger, ema, rsi

# 触发数低于此值即在 summary 标注「样本偏少、结论谨慎」（统计充分性裁决，§2.4）。
MIN_SIGNALS = 20


@dataclass
class SignalParams:
    """检测阈值（全部相对量纲，默认即生产值）。

    v1 工具面固定用默认值；``params=`` 仅为引擎 / 单测内部缝，不经工具 schema 透传（RV-3）。
    """

    breakout_win: int = 20
    vol_mult: float = 1.5
    rsi_period: int = 14
    rsi_low: float = 30.0
    rsi_high: float = 70.0
    boll_period: int = 20
    boll_std: float = 2.0
    ema_fast_p: int = 12
    ema_slow_p: int = 26
    warmup: int = 30
    min_bars: int = 60


# ============================ 信号库（8 个内置具名信号，§2.2 唯一真源 C6） ============================
def _detect_volume_breakout(df: pd.DataFrame, params: SignalParams) -> pd.Series:
    """放量突破前高：收盘破前 W 根最高 ∧ 放量（long）。"""
    high, close, vol = df["h"], df["c"], df["vol"]
    w = params.breakout_win
    prior_high = high.rolling(w).max().shift(1)
    vol_avg = vol.rolling(w).mean().shift(1)
    return (close > prior_high) & (vol >= params.vol_mult * vol_avg)


def _detect_volume_breakdown(df: pd.DataFrame, params: SignalParams) -> pd.Series:
    """放量跌破前低：收盘破前 W 根最低 ∧ 放量（short）。"""
    low, close, vol = df["l"], df["c"], df["vol"]
    w = params.breakout_win
    prior_low = low.rolling(w).min().shift(1)
    vol_avg = vol.rolling(w).mean().shift(1)
    return (close < prior_low) & (vol >= params.vol_mult * vol_avg)


def _detect_rsi_oversold(df: pd.DataFrame, params: SignalParams) -> pd.Series:
    """RSI 超卖下穿入：rsi 由 ≥ 阈值跌入 < 阈值（边沿触发，long）。"""
    series = rsi(df["c"], params.rsi_period)
    return (series < params.rsi_low) & (series.shift(1) >= params.rsi_low)


def _detect_rsi_overbought(df: pd.DataFrame, params: SignalParams) -> pd.Series:
    """RSI 超买上穿入：rsi 由 ≤ 阈值升入 > 阈值（边沿触发，short）。"""
    series = rsi(df["c"], params.rsi_period)
    return (series > params.rsi_high) & (series.shift(1) <= params.rsi_high)


def _detect_bollinger_lower_touch(df: pd.DataFrame, params: SignalParams) -> pd.Series:
    """新触及布林下轨：本根低点 ≤ 下轨且上一根低点 > 下轨（long）。"""
    _, _, lower = bollinger(df["c"], params.boll_period, params.boll_std)
    low = df["l"]
    return (low <= lower) & (low.shift(1) > lower.shift(1))


def _detect_bollinger_upper_touch(df: pd.DataFrame, params: SignalParams) -> pd.Series:
    """新触及布林上轨：本根高点 ≥ 上轨且上一根高点 < 上轨（short）。"""
    upper, _, _ = bollinger(df["c"], params.boll_period, params.boll_std)
    high = df["h"]
    return (high >= upper) & (high.shift(1) < upper.shift(1))


def _detect_ema_golden_cross(df: pd.DataFrame, params: SignalParams) -> pd.Series:
    """EMA 金叉：快线由下方上穿慢线（long）。"""
    fast = ema(df["c"], params.ema_fast_p)
    slow = ema(df["c"], params.ema_slow_p)
    return (fast >= slow) & (fast.shift(1) < slow.shift(1))


def _detect_ema_death_cross(df: pd.DataFrame, params: SignalParams) -> pd.Series:
    """EMA 死叉：快线由上方下穿慢线（short）。"""
    fast = ema(df["c"], params.ema_fast_p)
    slow = ema(df["c"], params.ema_slow_p)
    return (fast <= slow) & (fast.shift(1) > slow.shift(1))


@dataclass
class SignalSpec:
    """信号库单条目（key 隐含在 dict 键）。"""

    cn: str                                                      # 一句话中文定义（primer / summary 共用真源 C6）
    direction: str                                              # "long" | "short"
    detect: Callable[[pd.DataFrame, SignalParams], pd.Series]   # 纯检测函数，返回 bool Series


# 信号 key → 定义 / 方向 / 检测函数（detect_signal 用查找表替代 if 阶梯，圈复杂度 ≤10 R10）。
SIGNAL_LIBRARY: dict[str, SignalSpec] = {
    "volume_breakout": SignalSpec("放量突破前高", "long", _detect_volume_breakout),
    "volume_breakdown": SignalSpec("放量跌破前低", "short", _detect_volume_breakdown),
    "rsi_oversold": SignalSpec("RSI 超卖（下穿入）", "long", _detect_rsi_oversold),
    "rsi_overbought": SignalSpec("RSI 超买（上穿入）", "short", _detect_rsi_overbought),
    "bollinger_lower_touch": SignalSpec("触及布林下轨", "long", _detect_bollinger_lower_touch),
    "bollinger_upper_touch": SignalSpec("触及布林上轨", "short", _detect_bollinger_upper_touch),
    "ema_golden_cross": SignalSpec("EMA 金叉", "long", _detect_ema_golden_cross),
    "ema_death_cross": SignalSpec("EMA 死叉", "short", _detect_ema_death_cross),
}

# 信号 key → 候选策略 key（静态映射；运行时按 STRATEGY_REGISTRY 过滤掉不存在的 key 并补 name）。
SIGNAL_STRATEGY_HINTS: dict[str, list[str]] = {
    "volume_breakout": ["momentum_breakout"],
    "volume_breakdown": ["momentum_breakout"],
    "rsi_oversold": ["bollinger_reversion", "vwap_rsi_momentum"],
    "rsi_overbought": ["vwap_rsi_momentum"],
    "bollinger_lower_touch": ["bollinger_reversion"],
    "bollinger_upper_touch": ["bollinger_reversion"],
    "ema_golden_cross": ["ema_cross_scalper"],
    "ema_death_cross": ["ema_cross_scalper"],
}


@dataclass
class HorizonStat:
    """单个 horizon 的统计画像（字段即 payload 真源，camelCase 与前端对齐）。"""

    h: int
    count: int
    winRate: float
    meanRet: float
    medianRet: float
    stdRet: float
    tStat: float
    avgWin: float
    avgLoss: float
    payoff: float | None
    p25: float
    p50: float
    p75: float
    baselineRet: float
    edgeVsBaseline: float
    netMeanRet: float


@dataclass
class SignalEdgeResult:
    """信号前瞻收益结果（snapshot，无持久化）。"""

    signal: str
    direction: str
    entryAt: str
    costBps: float
    bars: int
    lastTs: int
    windowStartTs: int
    windowEndTs: int
    occurrences: int
    horizons: list[HorizonStat]
    candidateStrategies: list[dict]


# ============================ 信号检测 ============================
def detect_signal(df: pd.DataFrame, signal: str, params: SignalParams | None = None) -> pd.Series:
    """返回与 df 等长的 bool Series：第 i 根是否触发该具名信号（只用 ≤ i 数据，因果）。

    Args:
        df: ``candles_to_df`` 产物（列 ts/o/h/l/c/vol，升序）。
        signal: ``SIGNAL_LIBRARY`` 中的信号 key。
        params: 可选检测阈值；缺省即生产默认。

    Returns:
        与 ``df`` 等长、index 对齐 ``0..n-1`` 的 bool ``pd.Series``。

    Raises:
        ValueError: ``signal`` 不在 ``SIGNAL_LIBRARY``（工具层转 ok=False）。
    """
    params = params or SignalParams()
    if signal not in SIGNAL_LIBRARY:
        raise ValueError(f"未知信号：{signal}")
    work = df.reset_index(drop=True)
    raw = SIGNAL_LIBRARY[signal].detect(work, params)
    series = raw.fillna(False).astype(bool)
    if params.warmup > 0:
        series.iloc[:params.warmup] = False   # 指标未稳定（含 NaN）一律判 False，不补零、不外推
    return series


# ============================ 前瞻收益与统计 ============================
def _forward_return(prices: tuple, i: int, h: int, entry_at: str, direction: str) -> float:
    """第 i 根触发的方向调整前瞻收益（因果：入场 ≤ i+1、出场 i+h；调用方保证 i+h ≤ n-1）。"""
    opens, closes = prices
    entry = opens[i + 1] if entry_at == "next_open" else closes[i]
    exit_price = closes[i + h]
    if entry == 0:
        return 0.0
    raw = exit_price / entry - 1.0
    return raw if direction == "long" else -raw


def _horizon_returns(prices: tuple, triggers: np.ndarray, h: int, entry_at: str,
                     direction: str) -> tuple[list, list]:
    """逐 horizon 收益：(触发样本收益 sig, 全合格根收益 base)。

    右端因果截断：仅 ``i + h ≤ n-1`` 的根计入（窗口右端未走完的触发逐 horizon 各自剔除，
    绝不补零 C1）。base 在所有合格根（不论是否触发）上取方向调整收益，用于隔离市场漂移。
    """
    n = len(triggers)
    eligible = range(n - h) if n > h else range(0)
    base = [_forward_return(prices, i, h, entry_at, direction) for i in eligible]
    sig = [_forward_return(prices, i, h, entry_at, direction) for i in eligible if triggers[i]]
    return sig, base


def _empty_horizon_stat(h: int) -> HorizonStat:
    """空样本安全口径（RV-1 / RV-2）：count==0 时一律取 0.0 / None，绝不对空集 mean/median。"""
    return HorizonStat(h=h, count=0, winRate=0.0, meanRet=0.0, medianRet=0.0,
                       stdRet=0.0, tStat=0.0, avgWin=0.0, avgLoss=0.0, payoff=None,
                       p25=0.0, p50=0.0, p75=0.0, baselineRet=0.0,
                       edgeVsBaseline=0.0, netMeanRet=0.0)


def _summarize_horizon(h: int, sig: list, base: list, cost_bps: float) -> HorizonStat:
    """把单 horizon 的样本收益聚合成统计画像（空样本走安全口径，§2.4）。"""
    m = len(sig)
    if m == 0:
        return _empty_horizon_stat(h)
    arr = np.asarray(sig, dtype=float)
    wins, losses = arr[arr > 0.0], arr[arr < 0.0]
    mean = float(arr.mean())
    std = float(arr.std(ddof=1)) if m >= 2 else 0.0
    tstat = mean / (std / math.sqrt(m)) if (m >= 2 and std > 0.0) else 0.0
    avg_win = float(wins.mean()) if wins.size else 0.0
    avg_loss = float(losses.mean()) if losses.size else 0.0
    payoff = round(avg_win / abs(avg_loss), 3) if avg_loss != 0.0 else None
    baseline = float(np.mean(base)) if base else 0.0
    p25, p50, p75 = (float(x) for x in np.percentile(arr, [25, 50, 75]))
    return HorizonStat(
        h=h, count=m, winRate=round(float((arr > 0.0).mean()), 4),
        meanRet=round(mean, 6), medianRet=round(p50, 6), stdRet=round(std, 6),
        tStat=round(tstat, 3), avgWin=round(avg_win, 6), avgLoss=round(avg_loss, 6),
        payoff=payoff, p25=round(p25, 6), p50=round(p50, 6), p75=round(p75, 6),
        baselineRet=round(baseline, 6), edgeVsBaseline=round(mean - baseline, 6),
        netMeanRet=round(mean - cost_bps / 1e4, 6))


def forward_returns(df: pd.DataFrame, signal: str, *, horizons: tuple[int, ...] = (1, 3, 6, 12),
                    params: SignalParams | None = None, entry_at: str = "next_open",
                    cost_bps: float = 0.0) -> SignalEdgeResult:
    """对某具名信号度量逐 horizon 前瞻收益与显著性（严格因果 + 方向调整 + 基线对照）。

    Args:
        df: ``candles_to_df`` 产物（列 ts/o/h/l/c/vol，升序）。
        signal: ``SIGNAL_LIBRARY`` 中的信号 key。
        horizons: 前瞻根数（入聚合前去重并升序归一）。
        params: 可选检测阈值；缺省即生产默认。
        entry_at: ``next_open``（默认，入 open[i+1]，与回测撮合一致）或 ``signal_close``（入 close[i]）。
        cost_bps: 可选成本（基点）；>0 时额外给 ``netMeanRet``，主统计量仍建立在无成本收益上。

    Returns:
        ``SignalEdgeResult``（触发数 + 逐 horizon 统计 + 候选策略）。

    Raises:
        ValueError: ``signal`` 未知或可用根数 < ``params.min_bars``（不假数据 C1，工具层转 ok=False）。
    """
    params = params or SignalParams()
    if signal not in SIGNAL_LIBRARY:
        raise ValueError(f"未知信号：{signal}")
    work = df.reset_index(drop=True)
    if len(work) < params.min_bars:
        raise ValueError(f"数据不足：信号前瞻收益需 ≥{params.min_bars} 根，实得 {len(work)} 根")
    direction = SIGNAL_LIBRARY[signal].direction
    triggers = detect_signal(work, signal, params).to_numpy(dtype=bool)
    prices = (work["o"].to_numpy(dtype=float), work["c"].to_numpy(dtype=float))
    stats = []
    for h in sorted({int(value) for value in horizons}):
        sig, base = _horizon_returns(prices, triggers, h, entry_at, direction)
        stats.append(_summarize_horizon(h, sig, base, cost_bps))
    return SignalEdgeResult(
        signal=signal, direction=direction, entryAt=entry_at, costBps=float(cost_bps),
        bars=len(work), lastTs=int(work["ts"].iloc[-1]),
        windowStartTs=int(work["ts"].iloc[0]), windowEndTs=int(work["ts"].iloc[-1]),
        occurrences=int(triggers.sum()), horizons=stats,
        candidateStrategies=candidate_strategies_for_signal(signal))


# ============================ 信号目录 / 信号 → 候选策略 / 序列化 ============================
def available_signals() -> list[dict]:
    """信号目录 ``[{"key","cn","direction"}]``（供 system prompt primer 与 summary 共用真源 C6）。"""
    return [{"key": key, "cn": spec.cn, "direction": spec.direction}
            for key, spec in SIGNAL_LIBRARY.items()]


def candidate_strategies_for_signal(signal: str) -> list[dict]:
    """该信号若有 edge 可落成哪个内置策略；去重保序 + 按 STRATEGY_REGISTRY 过滤 + 补 name（R7）。

    Args:
        signal: 信号 key（未知 key 返回空列表，非错误）。

    Returns:
        ``[{"key","name"}]``（name 取自 ``STRATEGY_REGISTRY[key].meta.name``）。
    """
    seen: set[str] = set()
    out: list[dict] = []
    for key in SIGNAL_STRATEGY_HINTS.get(signal, []):
        if key in seen or key not in STRATEGY_REGISTRY:
            continue
        seen.add(key)
        out.append({"key": key, "name": STRATEGY_REGISTRY[key].meta.name})
    return out


def horizon_stat_to_dict(stat: HorizonStat) -> dict:
    """单 horizon 统计序列化为 camelCase dict（字段即 dataclass 字段，§5.1 唯一真源）。"""
    return asdict(stat)
