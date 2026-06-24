"""体制自适应策略组合的「净值轮动」回测核心（纯计算、无 IO）。

执行模型（净值轮动 net-value rotation）：每个成员先各自完整回测一次（沿用经测试的
``run_backtest``，真实风控与成本，行为同单策略页），得到逐根盯市净值 ``E_s[i]``；组合在
每个再平衡边界，从「与当前体制匹配的候选成员」中按近端表现选最优，按活跃成员当根收益率
复利累积组合净值，切换时扣一笔换手成本。

严格因果（平台硬规则 R4）：逐根体制标签只用「该根及之前」数据；再平衡决策只用
``regime[i-1]`` 与 ``scores@(i-1)``（滞后 1 根），选出的成员作用于第 ``i`` 根的组合收益。

为规避包级循环依赖（``strategies`` → ``engine.regime``/``engine.backtester`` → ``strategies.base``），
``run_backtest`` 与 ``regime`` 相关函数一律**函数内局部 import**。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .metrics import BARS_PER_YEAR, compute_metrics
from .simulator import EquityRecord, TradeRecord

if TYPE_CHECKING:  # 仅类型标注，零运行期 import（避免加载期环）。
    from ..strategies.base import BaseStrategy


@dataclass
class ComboPolicy:
    """轮动策略参数（抗过拟合 / 抗 whipsaw 的领域先验都落在这些标量上）。"""

    rebalance_bars: int = 24          # 再平衡间隔（根）
    lookback_bars: int = 168          # 近端表现回看窗口（根）
    selection_metric: str = "sharpe"  # "return" | "sharpe" | "calmar"
    min_dwell_bars: int = 24          # 选中成员最短持有（根），抑制频繁切换
    switch_margin: float = 0.10       # 迟滞：挑战者须超出在任者该相对优势才替换
    regime_gating: bool = True        # 仅在「与当前体制匹配的候选成员」内选优
    turnover_bps: float = 5.0         # 每次切换的换手成本（基点，按组合权益计）


@dataclass
class ComboMember:
    """组合中的一个成员（已实例化的原子策略快照）。"""

    key: str                  # 展示用 key（同模板多份带 #序号，仅用于展示/配色/时间线）
    template_key: str         # 注册表模板 key（体制 gating 一律用此字段，非展示 key，R2）
    name: str
    strategy: "BaseStrategy"  # 已实例化的成员（由 services 层构建注入）
    timeframes: list          # 该成员声明的高周期（用于取数并集）


@dataclass
class ComboSegment:
    """轮动时间线的一段（同一活跃成员的连续区间）。"""

    start_ts: int
    end_ts: int
    member_key: str
    member_name: str
    regime: str               # 决策时刻（i-1）的 primary 体制
    bars: int
    contribution: float       # 该段组合净值变动（结束/开始 - 1）


@dataclass
class ComboBacktestResult:
    """组合回测产物（与单策略 run 同构 + 轮动时间线/分体制贡献）。"""

    trades: list
    equity_points: list
    metrics: dict
    start_ts: int
    end_ts: int
    segments: list
    breakdown: list           # 分体制聚合：[{regime, memberKey, memberName, bars, contribution}]
    annualized_return: float
    notes: list


# ============================ 成员净值对齐 ============================
def _curve_from_equity(equity_points: list, ts_list: list[int], initial_capital: float) -> list[float]:
    """把成员逐根净值映射到基础 ts 索引；缺口前向填充上一净值（起点回落初始资金）。"""
    by_ts = {p.ts: p.equity for p in equity_points}
    curve: list[float] = []
    last = float(initial_capital)
    for ts in ts_list:
        if ts in by_ts:
            last = by_ts[ts]
        curve.append(last)
    return curve


def _build_member_curves(members: list[ComboMember], candles: list, bar: str, costs: tuple,
                         htf_frames_by_member: dict, ts_list: list[int]) -> tuple[dict, dict]:
    """对每个成员独立回测一次，返回 (memberKey→净值数组, memberKey→成交)。"""
    from .backtester import run_backtest   # 局部 import 规避加载期环
    initial_capital, taker_fee_bps, slippage_bps, leverage = costs
    curves: dict[str, list[float]] = {}
    trades: dict[str, list[TradeRecord]] = {}
    for member in members:
        result = run_backtest(
            member.strategy, candles, bar, initial_capital=initial_capital,
            taker_fee_bps=taker_fee_bps, slippage_bps=slippage_bps, leverage=leverage,
            htf_frames=htf_frames_by_member.get(member.key) or None)
        curves[member.key] = _curve_from_equity(result.equity_points, ts_list, initial_capital)
        trades[member.key] = result.trades
    return curves, trades


# ============================ 逐根体制标签（因果） ============================
def _per_bar_regime(df, regime_params) -> dict[int, str]:
    """逐根结构标签 → 按根索引的 dict；起算根之前（函数不产条目）补中性 range。"""
    from .regime import RegimeParams, per_bar_primaries   # 局部 import 规避加载期环
    params = regime_params or RegimeParams()
    regime = dict(per_bar_primaries(df, params))
    return {i: regime.get(i, "range") for i in range(len(df))}


def _eligible_keys(regime: str, members: list[ComboMember], gating: bool) -> tuple[list[str], bool]:
    """返回 (可选成员 key 列表, 是否因体制无命中而回退全体)。"""
    if not gating:
        return [m.key for m in members], False
    from .regime import REGIME_STRATEGY_HINTS   # 局部 import 规避加载期环
    hints = set(REGIME_STRATEGY_HINTS.get(regime, []))
    eligible = [m.key for m in members if m.template_key in hints]
    if eligible:
        return eligible, False
    return [m.key for m in members], True


# ============================ 近端表现打分（只读 ≤ end_idx 净值） ============================
def _window_returns(window: list[float]) -> list[float]:
    return [window[k] / window[k - 1] - 1.0 for k in range(1, len(window)) if window[k - 1] > 0]


def _sharpe(window: list[float]) -> float:
    rets = _window_returns(window)
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    std = var ** 0.5
    return mean / std if std > 0 else 0.0


def _calmar(window: list[float]) -> float:
    region_return = window[-1] / window[0] - 1.0
    peak = window[0]
    max_dd = 0.0
    for value in window:
        peak = max(peak, value)
        if peak > 0:
            max_dd = min(max_dd, value / peak - 1.0)
    return region_return / abs(max_dd) if max_dd < 0 else region_return


def _score(curve: list[float], end_idx: int, lookback: int, metric: str) -> float:
    """用 ``curve[max(0,end_idx-lookback)..end_idx]`` 计算近端表现（只读 ≤ end_idx）。"""
    lo = max(0, end_idx - lookback)
    window = curve[lo:end_idx + 1]
    if len(window) < 2 or window[0] <= 0:
        return 0.0
    if metric == "sharpe":
        return _sharpe(window)
    if metric == "calmar":
        return _calmar(window)
    return window[-1] / window[0] - 1.0


# ============================ 选优（迟滞 + 最短持有） ============================
def _select(prev_key, scores: dict, eligible_keys: list[str], policy: ComboPolicy,
            bars_held: int) -> str:
    """在 eligible 集合内选活跃成员；在任者未满最短持有或迟滞未越则保持。"""
    if not eligible_keys:
        return prev_key
    if prev_key in eligible_keys and bars_held < policy.min_dwell_bars:
        return prev_key
    challenger = max(eligible_keys, key=lambda k: scores.get(k, 0.0))
    if prev_key is None or prev_key not in eligible_keys:
        return challenger
    incumbent = scores.get(prev_key, 0.0)
    threshold = incumbent + abs(incumbent) * policy.switch_margin   # 双向迟滞，杜绝负底数发散
    return challenger if scores.get(challenger, 0.0) > threshold else prev_key


def _note_once(notes: list[str], message: str) -> None:
    if message not in notes:
        notes.append(message)


def _decide_member(i: int, members: list[ComboMember], curves: dict, regime_by_bar: dict,
                   policy: ComboPolicy, active_key, bars_held: int, notes: list[str]) -> str:
    """再平衡决策（滞后 1 根）：用 regime[i-1] 限定候选 + scores@(i-1) 选优。"""
    regime = regime_by_bar.get(i - 1, "range")
    eligible, fell_back = _eligible_keys(regime, members, policy.regime_gating)
    if fell_back:
        _note_once(notes, f"{regime} 体制无命中候选，回退全体成员")
    scores = {k: _score(curves[k], i - 1, policy.lookback_bars, policy.selection_metric)
              for k in eligible}
    return _select(active_key, scores, eligible, policy, bars_held)


# ============================ 组合净值拼接 ============================
def _accrue(eq_prev: float, curve: list[float], i: int, policy: ComboPolicy, switched: bool) -> float:
    """组合当根净值 = 上根净值 × (1 + 活跃成员当根收益)，切换则扣换手成本。"""
    prev_v = curve[i - 1] if i > 0 else curve[i]
    bar_return = (curve[i] / prev_v - 1.0) if prev_v > 0 else 0.0
    equity = eq_prev * (1.0 + bar_return)
    if switched:
        equity *= (1.0 - policy.turnover_bps / 1e4)
    return equity


def _open_segment(segs: list[dict], i: int, ts_list: list[int], active_key: str,
                  members: list[ComboMember], regime_by_bar: dict, eq_prev: float, eq: float) -> None:
    member = next((m for m in members if m.key == active_key), None)
    segs.append({
        "start_ts": ts_list[i], "end_ts": ts_list[i], "member_key": active_key,
        "member_name": member.name if member else active_key,
        "regime": regime_by_bar.get(i - 1, "range"),
        "bars": 1, "start_equity": eq_prev, "end_equity": eq,
    })


def _finalize_segments(segs: list[dict]) -> list[ComboSegment]:
    out: list[ComboSegment] = []
    for s in segs:
        start_eq = s["start_equity"]
        contribution = (s["end_equity"] / start_eq - 1.0) if start_eq > 0 else 0.0
        out.append(ComboSegment(
            start_ts=s["start_ts"], end_ts=s["end_ts"], member_key=s["member_key"],
            member_name=s["member_name"], regime=s["regime"], bars=s["bars"],
            contribution=contribution))
    return out


def _combine(members: list[ComboMember], curves: dict, regime_by_bar: dict, ts_list: list[int],
             policy: ComboPolicy, warmup_floor: int, initial_capital: float) -> tuple[list, list, list]:
    """逐根拼接组合净值 + 轮动时间线（返回 equity_points, segments, notes）。"""
    notes: list[str] = []
    points: list[EquityRecord] = []
    segs: list[dict] = []
    eq = peak = float(initial_capital)
    active = None
    last_rebalance = -1
    bars_held = 0
    for i in range(len(ts_list)):
        if i < warmup_floor:
            points.append(EquityRecord(ts=ts_list[i], equity=eq, drawdown=0.0))
            continue
        eq_prev = eq
        rebalance = active is None or (i - last_rebalance) >= policy.rebalance_bars
        if rebalance:
            decided = _decide_member(i, members, curves, regime_by_bar, policy, active, bars_held, notes)
            last_rebalance = i
        else:
            decided = active
        switched = active is not None and decided != active
        opened = decided != active
        active = decided
        eq = _accrue(eq_prev, curves[active], i, policy, switched)
        peak = max(peak, eq)
        points.append(EquityRecord(ts=ts_list[i], equity=eq,
                                   drawdown=min((eq / peak - 1.0) if peak > 0 else 0.0, 0.0)))
        if opened:
            _open_segment(segs, i, ts_list, active, members, regime_by_bar, eq_prev, eq)
            bars_held = 1
        else:
            segs[-1]["end_ts"] = ts_list[i]
            segs[-1]["end_equity"] = eq
            segs[-1]["bars"] += 1
            bars_held += 1
    return points, _finalize_segments(segs), notes


# ============================ 分体制贡献 / 组合成交 / 年化 ============================
def _breakdown_from_segments(segments: list[ComboSegment]) -> list[dict]:
    """按 (体制, 成员) 聚合根数与贡献（展示用，贡献按段简单累加）。"""
    agg: dict[tuple, dict] = {}
    order: list[tuple] = []
    for seg in segments:
        key = (seg.regime, seg.member_key)
        if key not in agg:
            agg[key] = {"regime": seg.regime, "memberKey": seg.member_key,
                        "memberName": seg.member_name, "bars": 0, "contribution": 0.0}
            order.append(key)
        agg[key]["bars"] += seg.bars
        agg[key]["contribution"] += seg.contribution
    return [agg[key] for key in order]


def _combo_trades(member_trades: dict, segments: list[ComboSegment]) -> list[TradeRecord]:
    """组合成交 = 各成员在其活跃区间内的成交（reason 前缀加成员 key），按 ts 升序。"""
    out: list[TradeRecord] = []
    for seg in segments:
        for trade in member_trades.get(seg.member_key, []):
            if seg.start_ts <= trade.ts <= seg.end_ts:
                out.append(replace(trade, reason=f"{seg.member_key}:{trade.reason}"))
    out.sort(key=lambda t: t.ts)
    return out


def _annualized_return(total_return: float, bar: str, effective_bars: int) -> float:
    """期望年化收益，带边界守卫（R3）：零有效根数→0；负底数（全损）→-1；杜绝 NaN/复数。"""
    if effective_bars <= 0:
        return 0.0
    base = 1.0 + total_return
    if base <= 0.0:
        return -1.0
    bars_per_year = BARS_PER_YEAR.get(bar, 8760)
    return base ** (bars_per_year / effective_bars) - 1.0


def _warmup_floor(members: list[ComboMember], policy: ComboPolicy, regime_params) -> int:
    """首个可决策根：max(各成员暖机, 逐根体制起算根, lookback 起算根)。"""
    from .regime import RegimeParams   # 局部 import 规避加载期环
    rp = regime_params or RegimeParams()
    regime_start = max(rp.ema_slow_p, rp.trend_win)   # 逐根体制起算根（非 min_bars）
    member_warmup = max((m.strategy.warmup() for m in members), default=0)
    return max(member_warmup, regime_start, policy.lookback_bars)


def run_combo_backtest(
    members: list[ComboMember],
    candles: list,
    bar: str,
    *,
    initial_capital: float,
    taker_fee_bps: float,
    slippage_bps: float,
    leverage: float,
    htf_frames_by_member: dict,
    policy: ComboPolicy,
    regime_params=None,
) -> ComboBacktestResult:
    """跑一个组合回测：成员净值对齐 → 逐根因果选优 → 组合净值/成交/时间线/指标。

    Args:
        members: 已实例化成员（≥2，由 services 层构建）。
        candles: 基础周期真实区间 K 线（升序）。
        bar: 标准 OKX 周期。
        initial_capital / taker_fee_bps / slippage_bps / leverage: 与单策略回测同口径。
        htf_frames_by_member: ``memberKey -> {htf_bar: 高周期 K 线}``（成员声明高周期时）。
        policy: 轮动参数。
        regime_params: 可选体制判定参数覆盖。

    Returns:
        ``ComboBacktestResult``（trades/equity/metrics + segments/breakdown + annualizedReturn）。
    """
    from .backtester import candles_to_df   # 局部 import 规避加载期环
    df = candles_to_df(candles)
    n = len(df)
    if n == 0:
        empty = compute_metrics([], [], bar, initial_capital)
        return ComboBacktestResult([], [], empty, 0, 0, [], [], 0.0, [])
    ts_list = [int(ts) for ts in df["ts"].tolist()]
    costs = (initial_capital, taker_fee_bps, slippage_bps, leverage)
    curves, member_trades = _build_member_curves(members, candles, bar, costs,
                                                  htf_frames_by_member, ts_list)
    regime_by_bar = _per_bar_regime(df, regime_params)
    warmup_floor = _warmup_floor(members, policy, regime_params)
    points, segments, notes = _combine(members, curves, regime_by_bar, ts_list, policy,
                                       warmup_floor, initial_capital)
    trades = _combo_trades(member_trades, segments)
    metrics = compute_metrics(points, trades, bar, initial_capital)
    effective_bars = max(0, n - warmup_floor)
    annualized = _annualized_return(metrics["totalReturn"], bar, effective_bars)
    return ComboBacktestResult(
        trades=trades, equity_points=points, metrics=metrics,
        start_ts=ts_list[0], end_ts=ts_list[-1], segments=segments,
        breakdown=_breakdown_from_segments(segments), annualized_return=annualized, notes=notes)
