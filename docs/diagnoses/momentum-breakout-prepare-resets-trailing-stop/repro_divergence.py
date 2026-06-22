"""最小复现：momentum_breakout 在「回测」与「实时 paper/live」下跑成两个策略。

根因：`MomentumBreakout.prepare()` 在 momentum_breakout.py:49-50 里把跟踪止损的运行态
`self._peak / self._trough` 置零。

- 回测路径 backtester.run_backtest()：`prepare()` 只在循环前调用 **一次**，
  `_peak/_trough` 由 `on_candle()` 跨整段持仓累积 → 跟踪止损按「持仓期间最高价」工作。
- 实时路径 paper/session._process_confirmed()：**每根已确认 K 线都重新调用一次 `prepare()`**
  （session.py:290），于是 `_peak` 每根都被重置为 0，`on_candle()` 里
  `self._peak = max(self._peak, h)` 退化成「当根最高价」，跟踪止损变成「当根盘中回撤」检查。

本脚本在 **同一组 K 线、同一参数、同一撮合引擎** 下对比三种配置：
  A = 真实回测 run_backtest()                （prepare 一次，对照真相）
  B = 忠实复刻的 paper 撮合循环 + prepare 每根 （现状 = bug）
  C = 同一 paper 循环但 prepare 只一次         （隔离变量：证明唯一差异就是 prepare 频次）

预期：A 与 C 完全一致（证明复刻管线无偏差）；B 与 C 不同（隔离出 bug）。

运行：  python docs/diagnoses/momentum-breakout-prepare-resets-trailing-stop/repro_divergence.py
（脚本自行把 services/quant 注入 sys.path，可从仓库根目录直接运行。）
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
QUANT = os.path.normpath(os.path.join(HERE, "..", "..", "..", "services", "quant"))
sys.path.insert(0, QUANT)

from app.engine.backtester import candles_to_df, run_backtest  # noqa: E402
from app.engine.simulator import SimBroker  # noqa: E402
from app.strategies.momentum_breakout import MomentumBreakout  # noqa: E402

BAR = "1m"
INIT_CAP = 10_000.0
# 手续费/滑点置 0，让价差与盈亏一目了然（不影响定性结论）。
FEE_BPS = 0.0
SLIP_BPS = 0.0


def build_candles() -> list[dict]:
    """构造一段：突破做多 → 冲到 120 见顶 → 缓慢阴跌回 98 的行情。

    设计要点：
    - 见顶后第 1 根（bar28）收盘恰好 = 峰值*(1-trail)=120*0.98=117.6 →
      回测（峰值=120）触发跟踪止损；paper（峰值=当根高=119）不触发。
    - 此后每根「当根高 - 收盘」< 2%，paper 的退化跟踪止损永不触发 →
      paper 一路扛单，直到硬止损（入场价 104 的 -3% = 100.88）才离场。
    """
    candles: list[dict] = []
    ts = 0

    def add(o: float, h: float, l: float, c: float, vol: float) -> None:
        nonlocal ts
        candles.append({"ts": ts, "o": o, "h": h, "l": l, "c": c, "vol": vol})
        ts += 60

    for _ in range(25):                 # 0..24 平台期，无突破
        add(100.0, 101.0, 99.0, 100.0, 100.0)
    add(100.0, 105.0, 100.0, 104.0, 300.0)   # 25 突破：涨 4% + 放量 → 做多信号
    add(104.0, 112.0, 104.0, 111.0, 150.0)   # 26 次根开盘成交@104，上行
    add(111.0, 120.0, 110.0, 119.0, 150.0)   # 27 见顶，峰值=120
    add(119.0, 119.0, 117.0, 117.6, 120.0)   # 28 回撤：c=117.6=120*0.98 → 回测触发跟踪止损
    grind = [                                  # 29.. 缓慢阴跌，当根振幅<2%
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
        (102.8, 102.8, 100.5, 100.8),   # l=100.5 ≤ 硬止损 100.88 → paper 在此被强平
        (100.8, 100.8, 99.0, 99.3),
        (99.3, 99.3, 98.0, 98.3),
    ]
    for o, h, l, c in grind:
        add(o, h, l, c, 120.0)
    for _ in range(3):                  # 末尾留几根，避免「最后一根不开仓」边界干扰
        add(98.3, 98.5, 98.0, 98.3, 100.0)
    return candles


def run_paper_loop(candles: list[dict], *, prepare_per_bar: bool) -> list:
    """忠实复刻 paper/session._process_confirmed 的撮合管线（去掉 DB/WS）。

    与 backtester.run_backtest 唯一可调差异：`prepare_per_bar` 控制 prepare 调用频次。
    其余四阶段顺序（fill_pending → apply_risk → mark → on_candle/submit）逐字对齐。
    """
    strat = MomentumBreakout()
    df0 = candles_to_df(candles)
    n = len(df0)
    risk = strat.risk_params()
    broker = SimBroker(INIT_CAP, FEE_BPS, SLIP_BPS, 1.0)
    warmup = max(strat.warmup(), 1)

    df_once = None if prepare_per_bar else strat.prepare(df0.copy(), BAR)
    for i in range(n):
        df = strat.prepare(df0.copy(), BAR) if prepare_per_bar else df_once
        row = df.iloc[i]
        broker.fill_pending(open_price=float(row["o"]), ts=int(row["ts"]), bar_index=i)
        broker.apply_risk(i, row, risk)
        broker.mark(ts=int(row["ts"]), close=float(row["c"]))
        if i >= warmup and i < n - 1:           # 与 backtester 同一边界
            ctx = broker.context()
            signals = strat.on_candle(df, i, ctx)
            if signals:
                broker.submit(signals)
    last = df0.iloc[-1]
    broker.finalize(ts=int(last["ts"]), close=float(last["c"]))
    return broker.trades


def fmt_trades(trades: list) -> str:
    lines = []
    for t in trades:
        lines.append(f"    ts={t.ts:>4}  {t.side:<5}  price={t.price:8.3f}  "
                     f"pnl={t.pnl:+9.2f}  reason={t.reason}")
    return "\n".join(lines) if lines else "    (无成交)"


def final_equity(trades: list) -> float:
    return INIT_CAP + sum(t.pnl for t in trades)


def main() -> None:
    candles = build_candles()

    # A：真实回测
    a = run_backtest(MomentumBreakout(), candles, BAR,
                     initial_capital=INIT_CAP, taker_fee_bps=FEE_BPS,
                     slippage_bps=SLIP_BPS, leverage=1.0)
    a_trades = a.trades
    # B：paper 现状（prepare 每根）= bug
    b_trades = run_paper_loop(candles, prepare_per_bar=True)
    # C：paper 但 prepare 一次（隔离变量）
    c_trades = run_paper_loop(candles, prepare_per_bar=False)

    print("=" * 78)
    print("A) 真实回测 run_backtest()  —— prepare 仅一次（对照真相）")
    print(fmt_trades(a_trades))
    print(f"  最终权益: {final_equity(a_trades):.2f}  (初始 {INIT_CAP:.0f})")
    print("=" * 78)
    print("B) paper 撮合循环 + prepare 每根  —— 现状 = BUG")
    print(fmt_trades(b_trades))
    print(f"  最终权益: {final_equity(b_trades):.2f}  (初始 {INIT_CAP:.0f})")
    print("=" * 78)
    print("C) paper 撮合循环 + prepare 一次  —— 隔离变量（仅 prepare 频次与 B 不同）")
    print(fmt_trades(c_trades))
    print(f"  最终权益: {final_equity(c_trades):.2f}  (初始 {INIT_CAP:.0f})")
    print("=" * 78)

    a_key = [(t.side, round(t.price, 3), t.reason) for t in a_trades]
    b_key = [(t.side, round(t.price, 3), t.reason) for t in b_trades]
    c_key = [(t.side, round(t.price, 3), t.reason) for t in c_trades]
    print(f"结论: A == C ? {a_key == c_key}   (复刻管线无偏差，prepare 一次时与回测逐笔一致)")
    print(f"      B == C ? {b_key == c_key}   (False = 唯一变量 prepare 频次导致策略跑成两套)")
    print(f"      回测盈亏 {final_equity(a_trades) - INIT_CAP:+.2f}  vs  "
          f"paper 盈亏 {final_equity(b_trades) - INIT_CAP:+.2f}")


if __name__ == "__main__":
    main()
