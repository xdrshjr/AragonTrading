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
