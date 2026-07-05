"""AI 回测扩展绩效指标（ai-auto-trading-backtest-analytics §2.2，纯函数）。

与 `engine/metrics.compute_metrics` **互补、独立**：从既有权益曲线 + 成交记录派生进阶
风险/质量指标（Sortino/Calmar/CAGR/年化波动/回撤时长/在场占比/盈亏比/连胜连亏/平均持仓/
交易频率）。**不改** `metrics.py`——`compute_metrics` 七项契约与所有既有回测调用方零影响。

口径与 `metrics.py` 逐字对齐（一致性强约束，防止「策略 vs 基准」指标漂移）：逐根收益率
`equity[i]/equity[i-1]-1`、样本方差 `ddof=1`、`BARS_PER_YEAR` 年化因子、平仓笔 `side=="close"`
选取——使夏普/波动率与 `metrics.py`、benchmark 三处逐位可比。年化夏普只有一处实现
`_annualized_sharpe`（benchmark 复用同一助手，禁止复制两份）。
"""
from __future__ import annotations

import math

from .metrics import BARS_PER_YEAR
from .mtf import bar_to_seconds
from .simulator import EquityRecord, TradeRecord

_WEEK_SECONDS = 604800


def _bar_returns(equities: list[float]) -> list[float]:
    """逐根收益率（与 metrics.py 同式：仅 prev!=0 才计入）。"""
    out: list[float] = []
    for prev, cur in zip(equities, equities[1:]):
        if prev != 0:
            out.append(cur / prev - 1.0)
    return out


def _sample_std(returns: list[float]) -> float:
    """样本标准差（ddof=1，与 metrics.py 夏普口径逐字一致）；len<2 → 0。"""
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(var)


def _annualized_sharpe(returns: list[float], bar: str) -> float:
    """年化夏普（**唯一实现**，benchmark 复用）：`mean/std*sqrt(BPY)`；与 metrics.py:41-46 逐字同式。"""
    std = _sample_std(returns)
    if std <= 0:
        return 0.0
    mean = sum(returns) / len(returns)
    return mean / std * math.sqrt(BARS_PER_YEAR.get(bar, 8760))


def _downside_dev(returns: list[float]) -> float:
    """下行标准差（Sortino 分母专用）：`sqrt(mean(min(r,0)²))`；空 → 0。"""
    if not returns:
        return 0.0
    downside = sum(min(r, 0.0) ** 2 for r in returns) / len(returns)
    return math.sqrt(downside)


def _annualized_return(final_equity: float, initial_capital: float, bars: int, bar: str) -> float:
    """年化收益（CAGR）：`base**(BPY/bars)-1`；`base<=0 → -1`（避免对负基数开分数次幂）。"""
    base = final_equity / initial_capital if initial_capital else 0.0
    if base <= 0:
        return -1.0
    return base ** (BARS_PER_YEAR.get(bar, 8760) / max(bars, 1)) - 1.0


def _max_drawdown_bars(equity_points: list[EquityRecord]) -> int:
    """最长水下持续根数：权益 `drawdown<0` 的最长连续段长度。"""
    longest = current = 0
    for p in equity_points:
        if p.drawdown < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _streaks(pnls: list[float]) -> tuple[int, int]:
    """平仓笔按序 pnl 符号的最长连胜 / 连亏（pnl==0 中断连段）。"""
    max_win = max_loss = win = loss = 0
    for pnl in pnls:
        if pnl > 0:
            win, loss = win + 1, 0
            max_win = max(max_win, win)
        elif pnl < 0:
            win, loss = 0, loss + 1
            max_loss = max(max_loss, loss)
        else:
            win = loss = 0
    return max_win, max_loss


def _paired_hold_bars(trades: list[TradeRecord], bar_sec: int) -> list[float]:
    """open→close 配对的持仓根数（反手视为「先平后开」两段）；未配对的开仓忽略。"""
    holds: list[float] = []
    open_ts: int | None = None
    for t in trades:
        if t.side in ("long", "short"):
            open_ts = t.ts
        elif t.side == "close" and open_ts is not None:
            holds.append((t.ts - open_ts) / bar_sec)
            open_ts = None
    return holds


def _risk_ratios(equity_points: list[EquityRecord], returns: list[float],
                 initial_capital: float, bar: str) -> dict:
    """风险调整类：年化收益/年化波动/Sortino/Calmar/回撤时长（camelCase 子 dict）。"""
    max_drawdown = min((p.drawdown for p in equity_points), default=0.0)
    ann_return = _annualized_return(equity_points[-1].equity, initial_capital,
                                    len(equity_points), bar)
    downside = _downside_dev(returns)
    mean = sum(returns) / len(returns) if returns else 0.0
    bpy_sqrt = math.sqrt(BARS_PER_YEAR.get(bar, 8760))
    dd_bars = _max_drawdown_bars(equity_points)
    return {
        "annualizedReturn": round(ann_return, 6),
        "annualizedVolatility": round(_sample_std(returns) * bpy_sqrt, 6),
        "sortino": round(mean / downside * bpy_sqrt, 6) if downside > 0 else 0.0,
        "calmar": round(ann_return / abs(max_drawdown), 6) if max_drawdown != 0 else 0.0,
        "maxDrawdownBars": dd_bars,
        "maxDrawdownSecs": dd_bars * bar_to_seconds(bar),
    }


def _quality_metrics(closed: list[TradeRecord]) -> dict:
    """交易质量类（平仓笔 `side=="close"` 同源 metrics.py:52）：平均盈亏/盈亏比/极值/连胜连亏。"""
    wins = [t.pnl for t in closed if t.pnl > 0]
    losses = [t.pnl for t in closed if t.pnl < 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    max_win, max_loss = _streaks([t.pnl for t in closed])
    return {
        "avgWin": round(avg_win, 6),
        "avgLoss": round(avg_loss, 6),
        "payoffRatio": round(avg_win / avg_loss, 6) if avg_loss > 0 else 0.0,
        "largestWin": round(max(wins, default=0.0), 6),
        "largestLoss": round(min(losses, default=0.0), 6),
        "maxConsecutiveWins": max_win,
        "maxConsecutiveLosses": max_loss,
    }


def compute_extended_metrics(
    equity_points: list[EquityRecord],
    trades: list[TradeRecord],
    bar: str,
    initial_capital: float,
    *,
    bars_in_market: int | None = None,
) -> dict:
    """从权益曲线 + 成交记录派生进阶风险/质量指标（camelCase），与 compute_metrics 互补。

    口径逐字对齐 metrics.py（ddof=1、BARS_PER_YEAR、平仓笔 side=="close"）。空/退化输入
    回落 0.0（annualizedReturn 在 base<=0 时 -1），**绝不抛异常**。

    Args:
        equity_points: SimBroker 逐根权益快照（含 drawdown ≤0）。
        trades: SimBroker 成交记录（side ∈ {long,short,close}，平仓笔带已实现 pnl）。
        bar: 标准 OKX 周期（年化因子与 bar→秒换算用）。
        initial_capital: 初始资金（年化收益基数）。
        bars_in_market: 运行器传入的在场根数（权威）；None → 从成交对推断兜底。

    Returns:
        含 annualizedReturn/annualizedVolatility/sortino/calmar/maxDrawdownBars/
        maxDrawdownSecs/timeInMarketPct/avgWin/avgLoss/payoffRatio/largestWin/largestLoss/
        maxConsecutiveWins/maxConsecutiveLosses/avgHoldBars/tradesPerWeek 的 dict。
    """
    if not equity_points:
        return _empty_extended()
    returns = _bar_returns([p.equity for p in equity_points])
    bars = len(equity_points)
    holds = _paired_hold_bars(trades, bar_to_seconds(bar))
    in_market = bars_in_market if bars_in_market is not None else sum(holds)
    span_secs = equity_points[-1].ts - equity_points[0].ts
    closed = [t for t in trades if t.side == "close"]
    return {
        **_risk_ratios(equity_points, returns, initial_capital, bar),
        "timeInMarketPct": round(in_market / bars, 6),
        "avgHoldBars": round(sum(holds) / len(holds), 6) if holds else 0.0,
        "tradesPerWeek": round(len(closed) / (span_secs / _WEEK_SECONDS), 6) if span_secs > 0 else 0.0,
        **_quality_metrics(closed),
    }


def _empty_extended() -> dict:
    """退化输入（无权益点）回落：全 0（int 键为 0），供调用方无分支渲染。"""
    return {
        "annualizedReturn": 0.0, "annualizedVolatility": 0.0, "sortino": 0.0, "calmar": 0.0,
        "maxDrawdownBars": 0, "maxDrawdownSecs": 0, "timeInMarketPct": 0.0,
        "avgWin": 0.0, "avgLoss": 0.0, "payoffRatio": 0.0, "largestWin": 0.0, "largestLoss": 0.0,
        "maxConsecutiveWins": 0, "maxConsecutiveLosses": 0, "avgHoldBars": 0.0, "tradesPerWeek": 0.0,
    }
