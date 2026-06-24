"""绩效指标 (spec §6.3，含夏普年化因子 C8)。"""
from __future__ import annotations

import math

from .simulator import EquityRecord, TradeRecord

# 每周期年化的 bars_per_year（以一年≈365 天计，C8）
BARS_PER_YEAR = {
    "1m": 525600,
    "5m": 105120,
    "15m": 35040,
    "30m": 17520,
    "1H": 8760,
    "4H": 2190,
    "1D": 365,
}


def compute_metrics(
    equity_points: list[EquityRecord],
    trades: list[TradeRecord],
    bar: str,
    initial_capital: float,
) -> dict:
    """从权益曲线 + 成交记录计算绩效指标。返回与 TS `Metrics` 对齐的 dict。"""
    if not equity_points:
        return _empty_metrics()

    equities = [p.equity for p in equity_points]
    total_return = equities[-1] / initial_capital - 1.0 if initial_capital else 0.0

    # 逐根收益率
    bar_returns: list[float] = []
    for prev, cur in zip(equities, equities[1:]):
        if prev != 0:
            bar_returns.append(cur / prev - 1.0)

    # 夏普（年化，无风险利率取 0；std==0 -> 0）
    sharpe = 0.0
    if len(bar_returns) >= 2:
        mean = sum(bar_returns) / len(bar_returns)
        var = sum((r - mean) ** 2 for r in bar_returns) / (len(bar_returns) - 1)
        std = math.sqrt(var)
        if std > 0:
            sharpe = mean / std * math.sqrt(BARS_PER_YEAR.get(bar, 8760))

    # 最大回撤（取曲线最小 drawdown）
    max_drawdown = min((p.drawdown for p in equity_points), default=0.0)

    # 成交统计：只看平仓笔（pnl 有意义）
    closed = [t for t in trades if t.side == "close"]
    trade_count = len(closed)
    wins = [t.pnl for t in closed if t.pnl > 0]
    losses = [t.pnl for t in closed if t.pnl < 0]
    win_rate = len(wins) / trade_count if trade_count else 0.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (gross_win if gross_win > 0 else 0.0)
    avg_pnl = sum(t.pnl for t in closed) / trade_count if trade_count else 0.0

    return {
        "totalReturn": round(total_return, 6),
        "winRate": round(win_rate, 6),
        "profitFactor": round(profit_factor, 6),
        "maxDrawdown": round(max_drawdown, 6),
        "sharpe": round(sharpe, 6),
        "tradeCount": trade_count,
        "avgPnl": round(avg_pnl, 6),
    }


# ============================ 高级回测：跨窗口聚合（spec §3.3） ============================
def compute_stability_score(returns: list[float], profitable_rate: float) -> float:
    """稳定性评分 ∈ [0,1]，越高越稳健（高级回测 v1 启发式，纯函数可单测）。

    consistency = 1/(1+cv)，cv = std/(|avg|+1e-9)（离散度惩罚，越分散越低）；
    score = 0.6*profitable_rate + 0.4*consistency，夹取 [0,1] 后 round(6)。
    全部窗口正收益且方差小 → 接近 1；盈亏参半且高离散 → 接近 0。

    Args:
        returns: 各窗口的 totalReturn 列表。
        profitable_rate: 盈利窗口占比 ∈ [0,1]。

    Returns:
        稳定性评分 ∈ [0,1]，保留 6 位小数。
    """
    if not returns:
        return 0.0
    avg = sum(returns) / len(returns)
    if len(returns) >= 2:
        var = sum((r - avg) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(var)
    else:
        std = 0.0
    cv = std / (abs(avg) + 1e-9)
    consistency = 1.0 / (1.0 + cv)
    score = 0.6 * profitable_rate + 0.4 * consistency
    return round(min(max(score, 0.0), 1.0), 6)


def _median(values: list[float]) -> float:
    """有序中位数（偶数个取中间两者均值）；空列表返回 0.0。"""
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return 0.0
    mid = n // 2
    return ordered[mid] if n % 2 == 1 else (ordered[mid - 1] + ordered[mid]) / 2.0


def summarize_windows(windows: list[dict], *, uncovered_bars: int = 0,
                      notes: list[str] | None = None) -> dict:
    """跨窗口聚合，返回与 TS `WalkForwardSummary` 对齐的 dict（spec §3.3 / §6.2）。

    returns 取各窗口 `metrics.totalReturn`；avgMaxDrawdown 取各窗口 `metrics.maxDrawdown` 均值（≤0）。

    Args:
        windows: `run_walk_forward` 产出的逐窗口结果（含 `metrics`）。
        uncovered_bars: 末尾被丢弃的尾段根数（v2·P1-2，随 summary 回包供前端提示）。
        notes: 用户可读提示（窗口偏短 / 数据不足一个窗口 / 截断 等，v2·P1-2）。

    Returns:
        含 windowCount / profitableWindows / profitableRate / medianReturn / avgReturn /
        worstReturn / bestReturn / avgMaxDrawdown / stabilityScore / uncoveredBars / notes 的 dict。
    """
    notes = list(notes or [])
    count = len(windows)
    if count == 0:
        return {"windowCount": 0, "profitableWindows": 0, "profitableRate": 0.0,
                "medianReturn": 0.0, "avgReturn": 0.0, "worstReturn": 0.0, "bestReturn": 0.0,
                "avgMaxDrawdown": 0.0, "stabilityScore": 0.0,
                "uncoveredBars": int(uncovered_bars), "notes": notes}
    returns = [float(w["metrics"]["totalReturn"]) for w in windows]
    drawdowns = [float(w["metrics"]["maxDrawdown"]) for w in windows]
    profitable = sum(1 for r in returns if r > 0)
    profitable_rate = profitable / count
    return {
        "windowCount": count,
        "profitableWindows": profitable,
        "profitableRate": round(profitable_rate, 6),
        "medianReturn": round(_median(returns), 6),
        "avgReturn": round(sum(returns) / count, 6),
        "worstReturn": round(min(returns), 6),
        "bestReturn": round(max(returns), 6),
        "avgMaxDrawdown": round(sum(drawdowns) / count, 6),
        "stabilityScore": compute_stability_score(returns, profitable_rate),
        "uncoveredBars": int(uncovered_bars),
        "notes": notes,
    }


def _empty_metrics() -> dict:
    return {
        "totalReturn": 0.0,
        "winRate": 0.0,
        "profitFactor": 0.0,
        "maxDrawdown": 0.0,
        "sharpe": 0.0,
        "tradeCount": 0,
        "avgPnl": 0.0,
    }
