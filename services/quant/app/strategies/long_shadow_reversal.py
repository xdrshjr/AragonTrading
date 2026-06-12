"""策略 1：长影线反转 `long_shadow_reversal` (spec §5.2)。

长下影 + 前段下跌 → 抄底做多；长上影 + 前段暴涨 → 做空。
"""
from __future__ import annotations

import pandas as pd

from ..engine.simulator import BrokerContext
from .base import BaseStrategy, Signal, StrategyMeta, register_strategy

_EPS = 1e-9


@register_strategy
class LongShadowReversal(BaseStrategy):
    meta = StrategyMeta(
        key="long_shadow_reversal",
        name="长影线反转",
        description="1H K 线出现长下影（前段下跌后）抄底做多，长上影（前段暴涨后）做空；触发价/止盈止损/最大持仓兜底。",
        default_params={
            "shadow_ratio": 2.0,
            "lookback": 6,
            "move_pct": 0.02,
            "stop_loss_pct": 0.02,
            "take_profit_pct": 0.04,
            "max_hold_bars": 12,
            "strength": 0.8,
        },
        params_schema={
            "shadow_ratio": {"type": "number", "default": 2.0, "min": 1.0, "max": 6.0, "step": 0.1, "label": "影线/实体比"},
            "lookback": {"type": "int", "default": 6, "min": 2, "max": 30, "step": 1, "label": "回看根数"},
            "move_pct": {"type": "number", "default": 0.02, "min": 0.005, "max": 0.2, "step": 0.005, "label": "前段涨跌阈值"},
            "stop_loss_pct": {"type": "number", "default": 0.02, "min": 0.005, "max": 0.1, "step": 0.005, "label": "止损%"},
            "take_profit_pct": {"type": "number", "default": 0.04, "min": 0.005, "max": 0.2, "step": 0.005, "label": "止盈%"},
            "max_hold_bars": {"type": "int", "default": 12, "min": 1, "max": 200, "step": 1, "label": "最大持仓根数"},
            "strength": {"type": "number", "default": 0.8, "min": 0.1, "max": 1.0, "step": 0.05, "label": "仓位比例"},
        },
    )

    def warmup(self) -> int:
        return int(self.params["lookback"]) + 2

    def on_candle(self, df: pd.DataFrame, i: int, ctx: BrokerContext) -> list[Signal]:
        if ctx.position_side != "flat":
            return []  # 持仓中由风控（止盈/止损/max_hold）管理
        lookback = int(self.params["lookback"])
        if i < lookback:
            return []
        o = df["o"].iat[i]; h = df["h"].iat[i]; lo = df["l"].iat[i]; c = df["c"].iat[i]
        body = abs(c - o) + _EPS
        lower_shadow = min(o, c) - lo
        upper_shadow = h - max(o, c)
        ratio = float(self.params["shadow_ratio"])
        move_pct = float(self.params["move_pct"])
        strength = float(self.params["strength"])

        prev_close = df["c"].iat[i - lookback]
        prior_move = (c - prev_close) / prev_close if prev_close else 0.0

        # 锤子形（长下影 + 收阳 + 前段下跌）→ 做多
        if lower_shadow / body >= ratio and c > o and prior_move <= -move_pct:
            return [Signal(side="long", strength=strength,
                           reason=f"长下影反转(下影/实体={lower_shadow / body:.1f})")]
        # 射击之星（长上影 + 收阴 + 前段暴涨）→ 做空
        if upper_shadow / body >= ratio and c < o and prior_move >= move_pct:
            return [Signal(side="short", strength=strength,
                           reason=f"长上影反转(上影/实体={upper_shadow / body:.1f})")]
        return []
