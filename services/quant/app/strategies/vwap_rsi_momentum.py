"""策略 5：VWAP + RSI 动量 `vwap_rsi_momentum` (spec §5.2，日内)。

站上当日 VWAP（UTC 自然日重置）且 RSI 上穿 50 做多；跌破 VWAP 且 RSI 下穿 50 做空。
仅日内周期有效（bar ≥ 1D 返回空信号）。
"""
from __future__ import annotations

import pandas as pd

from ..engine.indicators import rsi, vwap_session
from ..engine.simulator import BrokerContext
from ..okx.types import INTRADAY_BARS
from .base import BaseStrategy, Signal, StrategyMeta, register_strategy


@register_strategy
class VwapRsiMomentum(BaseStrategy):
    meta = StrategyMeta(
        key="vwap_rsi_momentum",
        name="VWAP+RSI 动量",
        description="价格站上当日 VWAP 且 RSI 上穿 50 做多、跌破 VWAP 且 RSI 下穿 50 做空。仅日内周期有效。",
        default_params={
            "rsi_period": 14,
            "rsi_mid": 50,
            "stop_loss_pct": 0.015,
            "take_profit_pct": 0.03,
            "max_hold_bars": 24,
            "strength": 0.8,
        },
        params_schema={
            "rsi_period": {"type": "int", "default": 14, "min": 2, "max": 50, "step": 1, "label": "RSI 周期"},
            "rsi_mid": {"type": "int", "default": 50, "min": 40, "max": 60, "step": 1, "label": "RSI 中轴"},
            "stop_loss_pct": {"type": "number", "default": 0.015, "min": 0.003, "max": 0.1, "step": 0.001, "label": "止损%"},
            "take_profit_pct": {"type": "number", "default": 0.03, "min": 0.003, "max": 0.2, "step": 0.001, "label": "止盈%"},
            "max_hold_bars": {"type": "int", "default": 24, "min": 1, "max": 200, "step": 1, "label": "最大持仓根数"},
            "strength": {"type": "number", "default": 0.8, "min": 0.1, "max": 1.0, "step": 0.05, "label": "仓位比例"},
        },
    )

    def warmup(self) -> int:
        return int(self.params["rsi_period"]) + 2

    def prepare(self, df: pd.DataFrame, bar: str) -> pd.DataFrame:
        df = df.copy()
        self._intraday = bar in INTRADAY_BARS
        df["vwap"] = vwap_session(df, bar)
        df["rsi"] = rsi(df["c"], int(self.params["rsi_period"]))
        return df

    def on_candle(self, df: pd.DataFrame, i: int, ctx: BrokerContext) -> list[Signal]:
        if not getattr(self, "_intraday", False) or i < 1:
            return []  # 非日内周期 VWAP 无意义（UI 提示切换周期）
        vwap = df["vwap"].iat[i]
        c = df["c"].iat[i]
        r0, r1 = df["rsi"].iat[i], df["rsi"].iat[i - 1]
        if any(pd.isna(x) for x in (vwap, r0, r1)):
            return []
        mid = float(self.params["rsi_mid"])

        # 持仓中：跌回/升回 VWAP 平
        if ctx.position_side == "long":
            if c < vwap:
                return [Signal(side="close", reason="跌回 VWAP")]
            return []
        if ctx.position_side == "short":
            if c > vwap:
                return [Signal(side="close", reason="升回 VWAP")]
            return []

        strength = float(self.params["strength"])
        rsi_up = r1 <= mid and r0 > mid
        rsi_down = r1 >= mid and r0 < mid
        if c > vwap and rsi_up:
            return [Signal(side="long", strength=strength, reason="站上VWAP+RSI上穿50")]
        if c < vwap and rsi_down:
            return [Signal(side="short", strength=strength, reason="跌破VWAP+RSI下穿50")]
        return []
