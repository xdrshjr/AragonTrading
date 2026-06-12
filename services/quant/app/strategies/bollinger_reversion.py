"""策略 4：布林带均值回归 `bollinger_reversion` (spec §5.2，建议 5m/15m)。

跌破下轨且 RSI 超卖 → 做多回归中轨；冲破上轨且 RSI 超买 → 做空。回到中轨平。
"""
from __future__ import annotations

import pandas as pd

from ..engine.indicators import bollinger, rsi
from ..engine.simulator import BrokerContext
from .base import BaseStrategy, Signal, StrategyMeta, register_strategy


@register_strategy
class BollingerReversion(BaseStrategy):
    meta = StrategyMeta(
        key="bollinger_reversion",
        name="布林带均值回归",
        description="价格跌破下轨且 RSI 超卖做多、冲破上轨且 RSI 超买做空，回到中轨平仓。高频，建议 5m/15m。",
        default_params={
            "period": 20,
            "std_mult": 2.0,
            "rsi_period": 14,
            "rsi_os": 30,
            "rsi_ob": 70,
            "stop_loss_pct": 0.02,
            "max_hold_bars": 36,
            "strength": 0.8,
        },
        params_schema={
            "period": {"type": "int", "default": 20, "min": 5, "max": 100, "step": 1, "label": "布林周期"},
            "std_mult": {"type": "number", "default": 2.0, "min": 1.0, "max": 4.0, "step": 0.1, "label": "标准差倍数"},
            "rsi_period": {"type": "int", "default": 14, "min": 2, "max": 50, "step": 1, "label": "RSI 周期"},
            "rsi_os": {"type": "int", "default": 30, "min": 5, "max": 50, "step": 1, "label": "RSI 超卖"},
            "rsi_ob": {"type": "int", "default": 70, "min": 50, "max": 95, "step": 1, "label": "RSI 超买"},
            "stop_loss_pct": {"type": "number", "default": 0.02, "min": 0.005, "max": 0.1, "step": 0.005, "label": "止损%"},
            "max_hold_bars": {"type": "int", "default": 36, "min": 1, "max": 300, "step": 1, "label": "最大持仓根数"},
            "strength": {"type": "number", "default": 0.8, "min": 0.1, "max": 1.0, "step": 0.05, "label": "仓位比例"},
        },
    )

    def warmup(self) -> int:
        return max(int(self.params["period"]), int(self.params["rsi_period"])) + 2

    def prepare(self, df: pd.DataFrame, bar: str) -> pd.DataFrame:
        df = df.copy()
        upper, ma, lower = bollinger(df["c"], int(self.params["period"]), float(self.params["std_mult"]))
        df["boll_upper"], df["boll_ma"], df["boll_lower"] = upper, ma, lower
        df["rsi"] = rsi(df["c"], int(self.params["rsi_period"]))
        return df

    def on_candle(self, df: pd.DataFrame, i: int, ctx: BrokerContext) -> list[Signal]:
        c = df["c"].iat[i]
        upper = df["boll_upper"].iat[i]; ma = df["boll_ma"].iat[i]; lower = df["boll_lower"].iat[i]
        r = df["rsi"].iat[i]
        if any(pd.isna(x) for x in (upper, ma, lower, r)):
            return []

        # 持仓中：回到中轨平
        if ctx.position_side == "long":
            if c >= ma:
                return [Signal(side="close", reason="回归中轨")]
            return []
        if ctx.position_side == "short":
            if c <= ma:
                return [Signal(side="close", reason="回归中轨")]
            return []

        strength = float(self.params["strength"])
        if c < lower and r < float(self.params["rsi_os"]):
            return [Signal(side="long", strength=strength, reason=f"跌破下轨+RSI{r:.0f}")]
        if c > upper and r > float(self.params["rsi_ob"]):
            return [Signal(side="short", strength=strength, reason=f"冲破上轨+RSI{r:.0f}")]
        return []
