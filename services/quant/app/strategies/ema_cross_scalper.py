"""策略 3：双 EMA 交叉剥头皮 `ema_cross_scalper` (spec §5.2，建议 5m/15m)。

快 EMA 上穿慢 EMA 做多，下穿做空（反手）；slope_filter 过滤震荡。
"""
from __future__ import annotations

import pandas as pd

from ..engine.indicators import ema
from ..engine.simulator import BrokerContext
from .base import BaseStrategy, Signal, StrategyMeta, register_strategy


@register_strategy
class EmaCrossScalper(BaseStrategy):
    meta = StrategyMeta(
        key="ema_cross_scalper",
        name="双EMA交叉剥头皮",
        description="快/慢 EMA 金叉做多、死叉做空（反手）；慢线斜率过滤震荡。高频，建议 5m/15m。",
        default_params={
            "fast": 9,
            "slow": 21,
            "slope_filter": 0.0,
            "stop_loss_pct": 0.015,
            "take_profit_pct": 0.03,
            "max_hold_bars": 36,
            "strength": 0.8,
        },
        params_schema={
            "fast": {"type": "int", "default": 9, "min": 2, "max": 50, "step": 1, "label": "快 EMA"},
            "slow": {"type": "int", "default": 21, "min": 5, "max": 200, "step": 1, "label": "慢 EMA"},
            "slope_filter": {"type": "number", "default": 0.0, "min": 0.0, "max": 0.01, "step": 0.0005, "label": "斜率过滤"},
            "stop_loss_pct": {"type": "number", "default": 0.015, "min": 0.003, "max": 0.1, "step": 0.001, "label": "止损%"},
            "take_profit_pct": {"type": "number", "default": 0.03, "min": 0.003, "max": 0.2, "step": 0.001, "label": "止盈%"},
            "max_hold_bars": {"type": "int", "default": 36, "min": 1, "max": 300, "step": 1, "label": "最大持仓根数"},
            "strength": {"type": "number", "default": 0.8, "min": 0.1, "max": 1.0, "step": 0.05, "label": "仓位比例"},
        },
    )

    def warmup(self) -> int:
        return int(self.params["slow"]) + 2

    def prepare(self, df: pd.DataFrame, bar: str) -> pd.DataFrame:
        df = df.copy()
        df["ema_fast"] = ema(df["c"], int(self.params["fast"]))
        df["ema_slow"] = ema(df["c"], int(self.params["slow"]))
        return df

    def on_candle(self, df: pd.DataFrame, i: int, ctx: BrokerContext) -> list[Signal]:
        if i < 1:
            return []
        f0, f1 = df["ema_fast"].iat[i], df["ema_fast"].iat[i - 1]
        s0, s1 = df["ema_slow"].iat[i], df["ema_slow"].iat[i - 1]
        if any(pd.isna(x) for x in (f0, f1, s0, s1)):
            return []
        slope_filter = float(self.params["slope_filter"])
        strength = float(self.params["strength"])
        slope = (s0 - s1) / s1 if s1 else 0.0

        cross_up = f1 <= s1 and f0 > s0
        cross_down = f1 >= s1 and f0 < s0
        if cross_up and slope >= slope_filter:
            return [Signal(side="long", strength=strength, reason="EMA 金叉")]
        if cross_down and slope <= -slope_filter:
            return [Signal(side="short", strength=strength, reason="EMA 死叉")]
        return []
