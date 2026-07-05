"""AI 回测买入持有基准与超额 alpha（ai-auto-trading-backtest-analytics §2.3，纯函数）。

在与策略权益曲线**相同的窗口**（用户区间 `main_df.iloc[first:]`）上模拟「首根收盘满仓买入
并持有至末根」，给出基准 equity/totalReturn/maxDrawdown/sharpe/annualizedReturn，回答
「AI 到底有没有跑赢什么都不做」。纯几何计算、无 LLM、O(bars)、确定性——收盘价单点、无前视。

`sharpe` 复用 `metrics_ext._annualized_sharpe`（与 metrics.py:41-46 逐字同式：ddof=1、同年化
因子），使「策略 sharpe（走 metrics.py）vs 基准 sharpe」逐位可比、不误导投资判断（禁止另写一份）。
"""
from __future__ import annotations

from .metrics_ext import _annualized_return, _annualized_sharpe, _bar_returns


def compute_buy_hold(closes: list[float], timestamps: list[int],
                     initial_capital: float, bar: str) -> dict:
    """区间买入持有基准：首根收盘满仓、逐根盯市至末根。

    Args:
        closes: 用户区间逐根收盘价（与策略权益曲线同窗口、等长）。
        timestamps: 与 closes 对齐的逐根 UTC 秒时间戳。
        initial_capital: 初始资金（与策略同基数，使 alpha 直接可比）。
        bar: 标准 OKX 周期（年化因子）。

    Returns:
        含 equity([{ts,equity,drawdown}]) / totalReturn / maxDrawdown / sharpe /
        annualizedReturn 的 dict；无法计算（空区间 / 首价<=0）时为 `{}`。
    """
    if len(closes) < 2 or closes[0] <= 0:            # 空 / 单点 / 首价<=0 → 无意义基准，回落 {}
        return {}
    units = initial_capital / closes[0]
    equity: list[dict] = []
    peak = 0.0
    for ts, close in zip(timestamps, closes):
        value = units * close
        peak = max(peak, value)
        drawdown = value / peak - 1.0 if peak > 0 else 0.0
        equity.append({"ts": int(ts), "equity": round(value, 4),
                       "drawdown": round(min(drawdown, 0.0), 6)})
    returns = _bar_returns([units * c for c in closes])
    return {
        "equity": equity,
        "totalReturn": round(closes[-1] / closes[0] - 1.0, 6),
        "maxDrawdown": round(min((p["drawdown"] for p in equity), default=0.0), 6),
        "sharpe": round(_annualized_sharpe(returns, bar), 6),
        "annualizedReturn": round(
            _annualized_return(units * closes[-1], initial_capital, len(closes), bar), 6),
    }
