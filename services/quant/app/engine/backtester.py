"""回测编排 (spec §6.2)。

流程：DataFrame(K线) → strategy.prepare(批量算指标) → 逐根 SimBroker 撮合 → metrics。
纯计算，无 IO；由 API 在 run_in_threadpool 中调用（R9）。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..strategies.base import BaseStrategy
from .metrics import compute_metrics
from .simulator import EquityRecord, RiskParams, SimBroker, TradeRecord


@dataclass
class BacktestResult:
    trades: list[TradeRecord]
    equity_points: list[EquityRecord]
    metrics: dict
    start_ts: int
    end_ts: int


def candles_to_df(candles: list) -> pd.DataFrame:
    """把归一化 Candle 列表（或 dict）转为回测用 DataFrame。"""
    rows = []
    for c in candles:
        if isinstance(c, dict):
            rows.append(c)
        else:
            rows.append({"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol})
    df = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "vol"])
    df = df.sort_values("ts").reset_index(drop=True)
    return df


def run_backtest(
    strategy: BaseStrategy,
    candles: list,
    bar: str,
    *,
    initial_capital: float,
    taker_fee_bps: float,
    slippage_bps: float,
    leverage: float = 1.0,
) -> BacktestResult:
    """在历史 K 线上跑完一个策略，返回成交 / 权益 / 指标。"""
    df = candles_to_df(candles)
    n = len(df)
    if n == 0:
        return BacktestResult([], [], compute_metrics([], [], bar, initial_capital), 0, 0)

    df = strategy.prepare(df, bar)
    risk = strategy.risk_params()
    broker = SimBroker(initial_capital, taker_fee_bps, slippage_bps, leverage)
    warmup = max(strategy.warmup(), 1)
    ctx = None

    for i in range(n):
        row = df.iloc[i]
        broker.fill_pending(open_price=float(row["o"]), ts=int(row["ts"]), bar_index=i)
        broker.apply_risk(i, row, risk)
        broker.mark(ts=int(row["ts"]), close=float(row["c"]))
        if i >= warmup and i < n - 1:  # 最后一根不再开新仓（无次根可成交）
            ctx = broker.context()
            signals = strategy.on_candle(df, i, ctx)
            if signals:
                broker.submit(signals)

    last = df.iloc[-1]
    broker.finalize(ts=int(last["ts"]), close=float(last["c"]))

    metrics = compute_metrics(broker.equity_points, broker.trades, bar, initial_capital)
    return BacktestResult(
        trades=broker.trades,
        equity_points=broker.equity_points,
        metrics=metrics,
        start_ts=int(df.iloc[0]["ts"]),
        end_ts=int(df.iloc[-1]["ts"]),
    )
