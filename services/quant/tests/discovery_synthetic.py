"""策略轴测试共用的**合成 K 线**与 ``LaneContext`` 装配（discovery-strategy-axes §7.1）。

纪律：所有回测用例一律用合成 K 线（正弦 + 固定种子噪声），**绝不连网**、绝不落库。
两段分布可控是 S6 双向用例的前提——「IS 强趋势 / OOS 震荡反转」必须能被构造出来。
"""
from __future__ import annotations

import math
import random

from app.config import Settings
from app.discovery.lanes import LaneContext
from app.engine.backtester import candles_to_df
from app.okx.types import Candle

TS0 = 1_700_000_000
STEP = 3600


def trend_candles(n: int, *, seed: int = 5, drift: float = 0.0015,
                  wave: float = 0.010, period: float = 9.0,
                  start: float = 100.0, ts0: int = TS0) -> list[Candle]:
    """确定性 K 线：漂移 + 正弦波 + 固定种子噪声（``drift`` 为每根的对数漂移近似）。"""
    rng = random.Random(seed)
    out: list[Candle] = []
    price = float(start)
    for i in range(n):
        move = drift + wave * math.sin(i / period) + 0.002 * (rng.random() - 0.5)
        close = price * (1.0 + move)
        out.append(Candle(ts=ts0 + i * STEP, o=round(price, 6),
                          h=round(max(price, close) * 1.003, 6),
                          l=round(min(price, close) * 0.997, 6), c=round(close, 6),
                          vol=round(1000.0 * (1.0 + 0.5 * math.sin(i / 5.0)) + 5.0 * i % 97,
                                    6)))
        price = close
    return out


def two_regime_candles(is_bars: int, oos_bars: int, *, seed: int = 5) -> list[Candle]:
    """IS 段强趋势、OOS 段反向震荡——S6「样本外崩塌」用例的构造器。"""
    head = trend_candles(is_bars, seed=seed, drift=0.0025, wave=0.008, period=11.0)
    tail_start = head[-1].c
    tail = trend_candles(oos_bars, seed=seed + 1, drift=-0.0022, wave=0.020, period=4.0,
                         start=tail_start, ts0=head[-1].ts + STEP)
    return head + tail


def lane_context(candles: list[Candle], *, split: int | None = None, bar: str = "1H",
                 config: dict | None = None, settings: Settings | None = None,
                 htf_frames: dict | None = None) -> LaneContext:
    df = candles_to_df(candles)
    cut = int(split if split is not None else int(len(candles) * 0.7))
    return LaneContext(df_full=df, candles=list(candles), htf_frames=htf_frames or {},
                       bar=bar, split=cut, split_ts=int(df["ts"].iloc[cut]),
                       config=config or {}, settings=settings or Settings(), memo={})
