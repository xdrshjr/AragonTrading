"""`momentum_breakout` 回归护栏：跟踪止损运行态不得被 `prepare()` 重置。

修复背景（见 docs/diagnoses/momentum-breakout-prepare-resets-trailing-stop/analysis.md）：
原 `prepare()` 在算完指标后把 `_peak/_trough` 置零。回测 `prepare()` 只调一次、无害；
paper/live 每根已确认 K 线都重调一次 `prepare()`，于是跟踪止损每根被抹掉，退化成「当根盘中
极值」——回测盈利、实盘扛单到硬止损。本文件把两条断言钉成护栏：
  1. 不变量：连续 `prepare()` 不改写 `_peak/_trough`。
  2. 等价性：paper「逐根 prepare」的成交序列 ≡ 回测「单次 prepare」的成交序列。
"""
from __future__ import annotations

import pandas as pd

from app.engine.backtester import candles_to_df, run_backtest
from app.engine.simulator import BrokerContext, SimBroker
from app.strategies.momentum_breakout import MomentumBreakout

BAR = "1m"
INIT_CAP = 10_000.0
FEE_BPS = 0.0
SLIP_BPS = 0.0


def _surge_then_grind_candles() -> list[dict]:
    """突破做多 → 冲到 120 见顶 → 缓慢阴跌回 98 的行情（复用诊断报告的复现数据）。

    见顶后第 1 根收盘恰为峰值*(1-trail)=120*0.98=117.6：真正的跟踪止损（按持仓期间峰值
    120 计）应在此触发离场；若运行态被逐根重置，跟踪止损退化、永不触发，一路扛到硬止损。
    """
    candles: list[dict] = []
    ts = 0

    def add(o: float, h: float, l: float, c: float, vol: float) -> None:
        nonlocal ts
        candles.append({"ts": ts, "o": o, "h": h, "l": l, "c": c, "vol": vol})
        ts += 60

    for _ in range(25):                       # 平台期，无突破
        add(100.0, 101.0, 99.0, 100.0, 100.0)
    add(100.0, 105.0, 100.0, 104.0, 300.0)    # 突破：涨 4% + 放量 → 做多
    add(104.0, 112.0, 104.0, 111.0, 150.0)
    add(111.0, 120.0, 110.0, 119.0, 150.0)    # 见顶，峰值=120
    add(119.0, 119.0, 117.0, 117.6, 120.0)    # 回撤：c=117.6=120*0.98 → 跟踪止损应触发
    grind = [                                  # 缓慢阴跌，当根振幅 < 2%
        (117.6, 117.6, 116.0, 116.2),
        (116.2, 116.2, 114.5, 114.8),
        (114.8, 114.8, 113.0, 113.3),
        (113.3, 113.3, 111.5, 111.8),
        (111.8, 111.8, 110.0, 110.3),
        (110.3, 110.3, 108.5, 108.8),
        (108.8, 108.8, 107.0, 107.3),
        (107.3, 107.3, 105.5, 105.8),
        (105.8, 105.8, 104.0, 104.3),
        (104.3, 104.3, 102.5, 102.8),
        (102.8, 102.8, 100.5, 100.8),
        (100.8, 100.8, 99.0, 99.3),
        (99.3, 99.3, 98.0, 98.3),
    ]
    for o, h, l, c in grind:
        add(o, h, l, c, 120.0)
    for _ in range(3):                        # 末尾留白，避开「最后一根不开仓」边界
        add(98.3, 98.5, 98.0, 98.3, 100.0)
    return candles


def _run_paper_per_bar(candles: list[dict]) -> list:
    """忠实复刻 paper/session 撮合管线（去 DB/WS），每根已确认 K 线重调一次 `prepare()`。

    四阶段顺序与 `backtester.run_backtest` / `paper/session._process_confirmed` 对齐：
    fill_pending → apply_risk → mark → on_candle/submit。
    """
    strat = MomentumBreakout()
    df0 = candles_to_df(candles)
    n = len(df0)
    risk = strat.risk_params()
    broker = SimBroker(INIT_CAP, FEE_BPS, SLIP_BPS, 1.0)
    broker.set_risk(risk)
    warmup = max(strat.warmup(), 1)
    for i in range(n):
        df = strat.prepare(df0.copy(), BAR)        # ← 逐根 prepare（paper/live 真实行为）
        row = df.iloc[i]
        broker.fill_pending(open_price=float(row["o"]), ts=int(row["ts"]), bar_index=i)
        broker.apply_risk(i, row, risk)
        broker.mark(ts=int(row["ts"]), close=float(row["c"]))
        if i >= warmup and i < n - 1:
            ctx = broker.context()
            signals = strat.on_candle(df, i, ctx)
            if signals:
                broker.submit(signals)
    last = df0.iloc[-1]
    broker.finalize(ts=int(last["ts"]), close=float(last["c"]))
    return broker.trades


def _trade_keys(trades: list) -> list[tuple]:
    return [(t.side, round(t.price, 3), t.reason) for t in trades]


def test_prepare_does_not_touch_runtime_state():
    """不变量：`prepare()` 是纯指标函数，连续调用都不得改写 `_peak/_trough`。"""
    strat = MomentumBreakout()
    df = candles_to_df(_surge_then_grind_candles())
    strat._peak = 123.0
    strat._trough = 45.0
    strat.prepare(df.copy(), BAR)
    strat.prepare(df.copy(), BAR)               # 第二次同样不得触碰
    assert strat._peak == 123.0
    assert strat._trough == 45.0


def test_trailing_peak_accumulates_under_per_bar_prepare():
    """逐根 prepare 下，持多仓期间 `_peak` 应跨根累积为真实峰值，而非被重置为当根高。"""
    strat = MomentumBreakout()
    df0 = candles_to_df(_surge_then_grind_candles())
    long_ctx = BrokerContext(position_side="long", bars_in_position=1,
                             entry_price=104.0, equity=INIT_CAP)
    strat._peak = 104.0
    peaks = []
    for i in range(26, 30):                     # 跨越 111→120→117.6 的持仓段
        df = strat.prepare(df0.copy(), BAR)     # 每根重算指标
        strat.on_candle(df, i, long_ctx)
        peaks.append(strat._peak)
    assert peaks == sorted(peaks)               # 单调不降
    assert max(peaks) >= 120.0                  # 峰值确被累积（=持仓期间最高价 120）


def test_paper_per_bar_prepare_matches_backtest():
    """等价性护栏：paper「逐根 prepare」的成交序列 ≡ 回测「单次 prepare」的成交序列。

    这是 analysis.md 的核心验收：把复现脚本里的 B(逐根) == A(回测) 从 False 锁成 True。
    回归一旦把运行态写回 `prepare()`，paper 跟踪止损退化、离场原因/价改变，此断言立即失败。
    """
    candles = _surge_then_grind_candles()
    backtest = run_backtest(MomentumBreakout(), candles, BAR, initial_capital=INIT_CAP,
                            taker_fee_bps=FEE_BPS, slippage_bps=SLIP_BPS, leverage=1.0)
    paper_trades = _run_paper_per_bar(candles)

    assert _trade_keys(paper_trades) == _trade_keys(backtest.trades)
    # 具体钉死：跟踪止损在 117.6 高位离场（而非退化后扛到硬止损 100.88）。
    closes = [t for t in paper_trades if t.side == "close"]
    assert len(closes) == 1
    assert closes[0].reason == "跟踪止损(多)"
    assert round(closes[0].price, 3) == 117.6
