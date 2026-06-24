"""行情体制（market regime）识别引擎 —— 纯计算、严格因果 (spec §2.3)。

三正交轴模型：结构轴（trend_up / trend_down / range）+ 波动轴（high / low_volatility）
+ 事件轴（crash / breakout）。全部判据用相对量纲（ER / ATR% 分位 / ATR 倍数 / 放量比），
不含绝对价位魔法数，跨标的跨周期稳健；指标一律复用 ``engine.indicators`` 纯函数。

不触网络 / DB / 磁盘 IO；经**包导入** ``from ..strategies import STRATEGY_REGISTRY``
触发 ``@register_strategy`` 副作用（同 ``db/repositories.py``），据此把体制→策略映射
过滤到真实存在的 key（P1-3）。``engine.regime`` 不被 ``engine/__init__`` 或任何策略反向
引入，故无环。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from ..strategies import STRATEGY_REGISTRY
from .indicators import atr, bollinger, ema, rsi

# 七标签 → 一句话定义（系统提示 primer 与工具脚手架的唯一真源，C6）。
REGIME_LABELS: dict[str, str] = {
    "trend_up": "上行趋势：方向效率高、均线多头排列、净变化为正的持续单边上行。",
    "trend_down": "下行趋势：方向效率高、均线空头排列、净变化为负的持续单边下行。",
    "range": "区间震荡：方向效率低、价格反复穿越中轴、无持续单边方向。",
    "high_volatility": "高波动：当前真实波幅（ATR%）处于近端高分位。",
    "low_volatility": "低波动：当前真实波幅处于近端低分位、带宽收口。",
    "crash": "急跌：短窗放量瀑布式下跌且 RSI 低位（合取条件，降误报）。",
    "breakout": "突破：放量有效突破前期区间端点（向上破前高或向下破前低）。",
}

# 体制标签 → 候选策略 key（静态映射；运行时按 STRATEGY_REGISTRY 过滤掉不存在的 key）。
REGIME_STRATEGY_HINTS: dict[str, list[str]] = {
    "trend_up": ["trend_pullback_long", "ema_cross_scalper", "momentum_breakout"],
    "trend_down": ["trend_pullback_short", "ema_cross_scalper"],
    "range": ["range_scalping_strategy", "bollinger_reversion"],
    "high_volatility": ["momentum_breakout", "vwap_rsi_momentum"],
    "low_volatility": ["range_scalping_strategy", "bollinger_reversion"],
    "breakout": ["momentum_breakout", "vwap_rsi_momentum"],
    "crash": ["trend_pullback_short", "long_shadow_reversal"],
}


@dataclass
class RegimeParams:
    """体制判定参数（全部相对量纲，默认即生产值，可被 execute 透传覆盖）。"""

    trend_win: int = 30
    ema_fast_p: int = 20
    ema_slow_p: int = 50
    atr_period: int = 14
    boll_period: int = 20
    rsi_period: int = 14
    baseline_win: int = 150
    crash_win: int = 12
    er_trend_min: float = 0.35
    vol_high_pct: float = 0.80
    vol_low_pct: float = 0.20
    crash_drop: float = -0.08
    crash_vol_pct: float = 0.85
    crash_rsi_max: float = 35.0
    breakout_atr_k: float = 0.25
    breakout_vol_mult: float = 1.5
    min_bars: int = 60


@dataclass
class RegimeEvidence:
    """底层证据数字（全部已四舍五入到展示精度；字段 camelCase 与前端对齐）。"""

    er: float
    netChange: float
    emaSlopePct: float
    fracAbove: float
    atrPct: float
    atrPctRank: float
    bbWidth: float
    bbWidthRank: float
    rsi: float
    fastDrop: float
    priorHigh: float
    priorLow: float
    volRatio: float
    bars: int
    lastTs: int
    lastClose: float


@dataclass
class RegimeResult:
    """单点体制判定结果（snapshot）。"""

    primary: str
    volatility: str
    event: str
    labels: list[str]
    scores: dict[str, float]
    confidence: float
    evidence: RegimeEvidence
    candidateStrategies: list[dict]


@dataclass
class RegimeSegment:
    """timeline 因果体制子段。"""

    startTs: int
    endTs: int
    primary: str
    labels: list[str]
    bars: int
    confidence: float


@dataclass
class _Frame:
    """一次判定内预算一次的趋势序列（避免逐根重算）。"""

    close: pd.Series
    diff_abs: pd.Series
    ema_fast: pd.Series
    ema_slow: pd.Series


# ============================ 通用小工具 ============================
def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def _pct_rank(values: pd.Series, window: int) -> float:
    """末值在近端 ``window`` 根（丢弃 NaN）内的分位（≤ 当前值的比例 ∈ [0,1]）。"""
    tail = values.dropna().tail(window)
    if tail.empty:
        return 0.5
    current = float(tail.iloc[-1])
    return float((tail <= current).sum()) / float(len(tail))


def _build_frame(df: pd.DataFrame, params: RegimeParams) -> _Frame:
    close = df["c"].astype(float)
    return _Frame(close=close, diff_abs=close.diff().abs(),
                  ema_fast=ema(close, params.ema_fast_p),
                  ema_slow=ema(close, params.ema_slow_p))


# ============================ ① 结构轴（趋势 / 方向） ============================
def _structural_inputs(frame: _Frame, i: int, w: int) -> tuple[float, float, bool, float]:
    """第 ``i`` 根的结构原始量：(ER, 净变化, 均线多头, 中轴上方占比)，只用 ≤ i 数据（因果）。"""
    prev = float(frame.close.iloc[i - w])
    cur = float(frame.close.iloc[i])
    net_change = (cur - prev) / prev if prev else 0.0
    path = float(frame.diff_abs.iloc[i - w + 1:i + 1].sum())
    er = abs(cur - prev) / path if path else 0.0
    align_up = bool(frame.ema_fast.iloc[i] >= frame.ema_slow.iloc[i])
    window_close = frame.close.iloc[i - w + 1:i + 1].to_numpy()
    window_slow = frame.ema_slow.iloc[i - w + 1:i + 1].to_numpy()
    frac_above = float((window_close > window_slow).sum()) / w
    return er, net_change, align_up, frac_above


def _decide_structure(er: float, net_change: float, align_up: bool,
                      params: RegimeParams) -> str:
    """结构轴离散判定（显式阈值，可单测、可复现）。"""
    if er >= params.er_trend_min and net_change > 0 and align_up:
        return "trend_up"
    if er >= params.er_trend_min and net_change < 0 and not align_up:
        return "trend_down"
    return "range"


def _trend_features(df: pd.DataFrame, params: RegimeParams) -> dict:
    frame = _build_frame(df, params)
    i = len(df) - 1
    w = params.trend_win
    er, net_change, align_up, frac_above = _structural_inputs(frame, i, w)
    slow_prev = float(frame.ema_slow.iloc[i - w])
    slope = (float(frame.ema_slow.iloc[i]) - slow_prev) / slow_prev if slow_prev else 0.0
    return {"er": er, "netChange": net_change, "emaSlopePct": slope,
            "fracAbove": frac_above,
            "primary": _decide_structure(er, net_change, align_up, params)}


# ============================ ② 波动轴 ============================
def _decide_volatility(rank: float, params: RegimeParams) -> str:
    if rank >= params.vol_high_pct:
        return "high_volatility"
    if rank <= params.vol_low_pct:
        return "low_volatility"
    return "normal"


def _volatility_features(df: pd.DataFrame, params: RegimeParams) -> dict:
    close = df["c"].astype(float)
    i = len(df) - 1
    atr_series = atr(df, params.atr_period)
    atr_pct = atr_series / close
    upper, ma, lower = bollinger(close, params.boll_period, 2.0)
    bb_width = (upper - lower) / ma
    atr_pct_rank = _pct_rank(atr_pct, params.baseline_win)
    return {"atrPct": float(atr_pct.iloc[i]), "atrPctRank": atr_pct_rank,
            "bbWidth": float(bb_width.iloc[i]),
            "bbWidthRank": _pct_rank(bb_width, params.baseline_win),
            "volatility": _decide_volatility(atr_pct_rank, params),
            "atrLast": float(atr_series.iloc[i])}


# ============================ ③ 事件轴（crash / breakout） ============================
def _detect_crash(fast_drop: float, atr_pct_rank: float, rsi_now: float,
                  params: RegimeParams) -> bool:
    """合取条件降误报：急跌 ∧ 波动骤升 ∧ 低 RSI。"""
    return (fast_drop <= params.crash_drop and atr_pct_rank >= params.crash_vol_pct
            and rsi_now < params.crash_rsi_max)


def _detect_breakout(close_last: float, edges: tuple[float, float], atr_last: float,
                     vol_ratio: float, params: RegimeParams) -> tuple[bool, float]:
    """返回 (是否突破, 突破距离 ATR 倍数)；需有效破位 ∧ 放量。"""
    prior_high, prior_low = edges
    if atr_last <= 0 or vol_ratio < params.breakout_vol_mult:
        return False, 0.0
    margin = params.breakout_atr_k * atr_last
    if close_last > prior_high + margin:
        return True, abs(close_last - prior_high) / atr_last
    if close_last < prior_low - margin:
        return True, abs(close_last - prior_low) / atr_last
    return False, 0.0


def _event_features(df: pd.DataFrame, params: RegimeParams, atr_pct_rank: float,
                    atr_last: float) -> dict:
    close = df["c"].astype(float)
    i = len(df) - 1
    w = params.trend_win
    cw = params.crash_win
    base = float(close.iloc[i - cw])
    fast_drop = (float(close.iloc[i - cw + 1:i + 1].min()) - base) / base if base else 0.0
    rsi_now = float(rsi(close, params.rsi_period).iloc[i])
    prior_high = float(df["h"].astype(float).iloc[i - w:i].max())
    prior_low = float(df["l"].astype(float).iloc[i - w:i].min())
    vol_mean = float(df["vol"].astype(float).iloc[i - w:i].mean())
    vol_ratio = float(df["vol"].astype(float).iloc[i]) / vol_mean if vol_mean else 0.0
    is_break, break_dist = _detect_breakout(
        float(close.iloc[i]), (prior_high, prior_low), atr_last, vol_ratio, params)
    # crash 与 breakout 同时满足时，以更紧迫的 crash 优先。
    event = ("crash" if _detect_crash(fast_drop, atr_pct_rank, rsi_now, params)
             else "breakout" if is_break else "none")
    return {"fastDrop": fast_drop, "rsi": rsi_now, "priorHigh": prior_high,
            "priorLow": prior_low, "volRatio": vol_ratio, "event": event,
            "breakDistAtr": break_dist if event == "breakout" else 0.0}


# ============================ 打分 + 组装 ============================
def _compute_scores(trend: dict, vol: dict, event: dict, params: RegimeParams) -> dict:
    """连续置信度（与离散判定一致：argmax(结构子集) 必等于 primary）。"""
    er = trend["er"]
    trend_conf = _clamp((er - params.er_trend_min) / (0.85 - params.er_trend_min), 0.0, 1.0)
    range_score = (_clamp((params.er_trend_min - er) / params.er_trend_min, 0.0, 1.0)
                   * (1.0 - 2.0 * abs(trend["fracAbove"] - 0.5)))
    break_score = (_clamp(event["breakDistAtr"], 0.0, 1.0)
                   * _clamp((event["volRatio"] - 1.0) / (params.breakout_vol_mult - 1.0),
                            0.0, 1.0))
    scores = {
        "trend_up": trend_conf if trend["primary"] == "trend_up" else 0.0,
        "trend_down": trend_conf if trend["primary"] == "trend_down" else 0.0,
        "range": range_score,
        "high_volatility": _clamp((vol["atrPctRank"] - params.vol_high_pct)
                                  / (1.0 - params.vol_high_pct), 0.0, 1.0),
        "low_volatility": _clamp((params.vol_low_pct - vol["atrPctRank"])
                                 / params.vol_low_pct, 0.0, 1.0),
        "crash": _clamp(event["fastDrop"] / params.crash_drop, 0.0, 1.0)
        if event["event"] == "crash" else 0.0,
        "breakout": break_score if event["event"] == "breakout" else 0.0,
    }
    return {key: round(value, 4) for key, value in scores.items()}


def _labels_of(primary: str, volatility: str, event: str) -> list[str]:
    labels = [primary]
    if volatility != "normal":
        labels.append(volatility)
    if event != "none":
        labels.append(event)
    return labels


def _build_evidence(df: pd.DataFrame, trend: dict, vol: dict, event: dict) -> RegimeEvidence:
    i = len(df) - 1
    return RegimeEvidence(
        er=round(trend["er"], 4), netChange=round(trend["netChange"], 4),
        emaSlopePct=round(trend["emaSlopePct"], 4), fracAbove=round(trend["fracAbove"], 4),
        atrPct=round(vol["atrPct"], 5), atrPctRank=round(vol["atrPctRank"], 3),
        bbWidth=round(vol["bbWidth"], 5), bbWidthRank=round(vol["bbWidthRank"], 3),
        rsi=round(event["rsi"], 2), fastDrop=round(event["fastDrop"], 4),
        priorHigh=round(event["priorHigh"], 4), priorLow=round(event["priorLow"], 4),
        volRatio=round(event["volRatio"], 3), bars=len(df),
        lastTs=int(df["ts"].iloc[i]), lastClose=round(float(df["c"].iloc[i]), 4))


def _assemble_result(df: pd.DataFrame, trend: dict, vol: dict, event: dict,
                     params: RegimeParams) -> RegimeResult:
    primary, volatility, ev = trend["primary"], vol["volatility"], event["event"]
    labels = _labels_of(primary, volatility, ev)
    scores = _compute_scores(trend, vol, event, params)
    return RegimeResult(
        primary=primary, volatility=volatility, event=ev, labels=labels, scores=scores,
        confidence=scores.get(primary, 0.0), evidence=_build_evidence(df, trend, vol, event),
        candidateStrategies=candidate_strategies(labels))


def classify_regime(df: pd.DataFrame, bar: str,
                    params: RegimeParams | None = None) -> RegimeResult:
    """对窗口末根做单点体制判定（严格因果，只用 ≤ 末根的数据）。

    Args:
        df: ``candles_to_df`` 产物（列 ts/o/h/l/c/vol，升序）。
        bar: 标准 OKX 周期（保留以备未来按周期调参；当前判据全相对量纲，不分周期）。
        params: 可选参数覆盖，缺省即生产默认。

    Returns:
        ``RegimeResult``（primary/volatility/event + labels + scores + 证据 + 候选策略）。

    Raises:
        ValueError: 可用根数 < ``params.min_bars``（不假数据，C1；工具层转 ok=False）。
    """
    params = params or RegimeParams()
    if len(df) < params.min_bars:
        raise ValueError(f"数据不足：体制判定需 ≥{params.min_bars} 根，实得 {len(df)} 根")
    df = df.reset_index(drop=True)
    trend = _trend_features(df, params)
    vol = _volatility_features(df, params)
    event = _event_features(df, params, vol["atrPctRank"], vol["atrLast"])
    return _assemble_result(df, trend, vol, event, params)


# ============================ timeline 体制子段 ============================
def _per_bar_primaries(df: pd.DataFrame, params: RegimeParams) -> list[tuple[int, str]]:
    """逐根结构标签（每根只用「该根及之前」的数据，严格因果）。"""
    frame = _build_frame(df, params)
    w = params.trend_win
    start = max(params.ema_slow_p, w)
    out: list[tuple[int, str]] = []
    for i in range(start, len(df)):
        er, net_change, align_up, _ = _structural_inputs(frame, i, w)
        out.append((i, _decide_structure(er, net_change, align_up, params)))
    return out


def per_bar_primaries(df: pd.DataFrame, params: RegimeParams | None = None) -> list[tuple[int, str]]:
    """逐根结构标签的公开稳定包装（组合层轮动复用，严格因果）。

    Args:
        df: ``candles_to_df`` 产物（升序）。
        params: 可选体制参数覆盖，缺省即生产默认。

    Returns:
        ``list[(i, primary)]``（升序，仅含第 ``max(ema_slow_p, trend_win)`` 根起的条目；
        起算根之前不产条目，由调用方按需补中性标签）。
    """
    params = params or RegimeParams()
    return _per_bar_primaries(df.reset_index(drop=True), params)


def _merge_runs(primaries: list[tuple[int, str]]) -> list[dict]:
    """相邻同结构标签合并为原始子段。"""
    runs: list[dict] = []
    for i, primary in primaries:
        if runs and runs[-1]["primary"] == primary:
            runs[-1]["end"] = i
            runs[-1]["bars"] += 1
        else:
            runs.append({"primary": primary, "start": i, "end": i, "bars": 1})
    return runs


def _coalesce(runs: list[dict]) -> list[dict]:
    out: list[dict] = []
    for run in runs:
        if out and out[-1]["primary"] == run["primary"]:
            out[-1]["end"] = run["end"]
            out[-1]["bars"] += run["bars"]
        else:
            out.append(dict(run))
    return out


def _absorb_short(runs: list[dict], min_seg_bars: int) -> list[dict]:
    """过短子段（< min_seg_bars）并入前一主段。"""
    if len(runs) <= 1:
        return [dict(r) for r in runs]
    merged = [dict(runs[0])]
    for run in runs[1:]:
        if run["bars"] < min_seg_bars:
            merged[-1]["end"] = run["end"]
            merged[-1]["bars"] += run["bars"]
        else:
            merged.append(dict(run))
    return merged


def _merge_pair(runs: list[dict], lo: int) -> None:
    """把相邻 (lo, lo+1) 两段合并为一段（保留较长段的 primary）。"""
    a, b = runs[lo], runs[lo + 1]
    primary = a["primary"] if a["bars"] >= b["bars"] else b["primary"]
    runs[lo] = {"primary": primary, "start": a["start"], "end": b["end"],
                "bars": a["bars"] + b["bars"]}
    del runs[lo + 1]


def _cap_segments(runs: list[dict], max_segments: int) -> list[dict]:
    """超 max_segments 时反复把最短段并入相邻段（按时长合并）。"""
    capped = [dict(r) for r in runs]
    while len(capped) > max_segments:
        idx = min(range(len(capped)), key=lambda k: capped[k]["bars"])
        lo = idx - 1 if idx == len(capped) - 1 else idx
        _merge_pair(capped, lo)
    return capped


def count_structural_runs(df: pd.DataFrame, params: RegimeParams | None = None) -> int:
    """相邻同结构标签的原始段数（未做短段吸收 / 上限合并）。

    用于判定 timeline 是否实际发生合并（``len(segments) < 该值`` 即已合并）。
    """
    params = params or RegimeParams()
    return len(_merge_runs(_per_bar_primaries(df.reset_index(drop=True), params)))


def _segment_label(df: pd.DataFrame, run: dict, bar: str,
                   params: RegimeParams) -> tuple[list[str], float]:
    """以子段结束根（仅用其及之前的数据）补全段标签与置信。"""
    end = run["end"]
    if end + 1 < params.min_bars:
        return [run["primary"]], 0.0
    res = classify_regime(df.iloc[:end + 1], bar, params)
    if res.primary == run["primary"]:
        return res.labels, res.confidence
    return [run["primary"]], res.confidence


def segment_regime(df: pd.DataFrame, bar: str, params: RegimeParams | None = None, *,
                   max_segments: int = 12, min_seg_bars: int = 8) -> list[RegimeSegment]:
    """把窗口因果切成体制子段（支撑「按 regime 拆开看」）。

    Args:
        df: ``candles_to_df`` 产物（升序）。
        bar: 标准 OKX 周期。
        params: 可选参数覆盖。
        max_segments: 子段数上限（超出按时长合并最短段）。
        min_seg_bars: 过短子段阈值（并入相邻主段）。

    Returns:
        ``RegimeSegment`` 列表（按时间升序，至多 ``max_segments`` 段）。

    Raises:
        ValueError: 可用根数 < ``params.min_bars``（不假数据，C1）。
    """
    params = params or RegimeParams()
    if len(df) < params.min_bars:
        raise ValueError(f"数据不足：体制时间线需 ≥{params.min_bars} 根，实得 {len(df)} 根")
    df = df.reset_index(drop=True)
    runs = _merge_runs(_per_bar_primaries(df, params))
    runs = _coalesce(_absorb_short(runs, min_seg_bars))
    runs = _coalesce(_cap_segments(runs, max_segments))
    segments: list[RegimeSegment] = []
    for run in runs:
        labels, confidence = _segment_label(df, run, bar, params)
        segments.append(RegimeSegment(
            startTs=int(df["ts"].iloc[run["start"]]), endTs=int(df["ts"].iloc[run["end"]]),
            primary=run["primary"], labels=labels, bars=run["bars"], confidence=confidence))
    return segments


# ============================ 体制 → 候选策略 ============================
def candidate_strategies(labels: list[str]) -> list[dict]:
    """按体制标签给候选策略；去重保序，并按 STRATEGY_REGISTRY 过滤掉不存在的 key（P1-3 / R7）。

    Args:
        labels: 体制标签列表（如 ``["trend_up", "high_volatility"]``）。

    Returns:
        ``[{"key", "name"}]``（name 取自 ``STRATEGY_REGISTRY[key].meta.name``）。
    """
    seen: set[str] = set()
    out: list[dict] = []
    for label in labels:
        for key in REGIME_STRATEGY_HINTS.get(label, []):
            if key in seen or key not in STRATEGY_REGISTRY:
                continue
            seen.add(key)
            out.append({"key": key, "name": STRATEGY_REGISTRY[key].meta.name})
    return out


def evidence_to_dict(evidence: RegimeEvidence) -> dict:
    """证据序列化为 camelCase dict（字段即 dataclass 字段，§5.1 唯一真源）。"""
    return asdict(evidence)


def segment_to_dict(segment: RegimeSegment) -> dict:
    return asdict(segment)
