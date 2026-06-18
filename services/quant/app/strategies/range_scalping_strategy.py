"""Ultra-short range scalping strategy over causal 5m/15m structure."""
from __future__ import annotations

import pandas as pd

from ..engine.simulator import BrokerContext
from ..okx.types import BAR_SECONDS
from ._range_structure import (
    RangeStructureConfig,
    attach_causal_range,
    attach_execution_flags,
)
from .base import BaseStrategy, Signal, StrategyMeta, register_strategy


@register_strategy
class RangeScalpingStrategy(BaseStrategy):
    meta = StrategyMeta(
        key="range_scalping_strategy",
        name="Range Scalping",
        description=(
            "Simulation-only ultra-short range scalping. Run on 1m candles; "
            "the strategy detects stable 5m/15m RSI and Bollinger ranges, "
            "shorts upper rejections, buys lower rejections, and exits at "
            "the range midpoint or structural invalidation."
        ),
        default_params={
            "execution_seconds": 60,
            "range_seconds": 300,
            "range_lookback": 12,
            "boll_period": 20,
            "boll_std_mult": 2.0,
            "boll_width_atr_max": 2.2,
            "rsi_period": 14,
            "rsi_mid": 50,
            "rsi_band": 12,
            "atr_period": 14,
            "width_atr_max": 3.0,
            "entry_zone_atr_mult": 0.35,
            "touch_atr_mult": 0.10,
            "wick_ratio": 1.5,
            "stop_atr_mult": 0.6,
            "stop_loss_pct": 0.02,
            "max_hold_bars": 30,
            "strength": 0.7,
        },
        params_schema={
            "range_seconds": {
                "type": "int", "default": 300, "min": 300, "max": 900,
                "step": 600, "label": "Range bucket seconds",
            },
            "range_lookback": {
                "type": "int", "default": 12, "min": 4, "max": 48,
                "step": 1, "label": "Range lookback buckets",
            },
            "boll_period": {
                "type": "int", "default": 20, "min": 4, "max": 80,
                "step": 1, "label": "Bollinger period",
            },
            "boll_std_mult": {
                "type": "number", "default": 2.0, "min": 1.0, "max": 4.0,
                "step": 0.1, "label": "Bollinger std multiplier",
            },
            "rsi_period": {
                "type": "int", "default": 14, "min": 4, "max": 50,
                "step": 1, "label": "RSI period",
            },
            "rsi_band": {
                "type": "number", "default": 12, "min": 2, "max": 30,
                "step": 1, "label": "RSI center band",
            },
            "entry_zone_atr_mult": {
                "type": "number", "default": 0.35, "min": 0.05, "max": 2.0,
                "step": 0.05, "label": "Entry zone ATR",
            },
            "touch_atr_mult": {
                "type": "number", "default": 0.10, "min": 0.0, "max": 1.0,
                "step": 0.05, "label": "Wick touch ATR",
            },
            "stop_atr_mult": {
                "type": "number", "default": 0.6, "min": 0.1, "max": 3.0,
                "step": 0.1, "label": "Structural stop ATR",
            },
            "max_hold_bars": {
                "type": "int", "default": 30, "min": 1, "max": 300,
                "step": 1, "label": "Max hold bars",
            },
            "strength": {
                "type": "number", "default": 0.7, "min": 0.1, "max": 1.0,
                "step": 0.05, "label": "Position strength",
            },
        },
    )

    def warmup(self) -> int:
        p = self.params
        range_bars_needed = max(
            int(p["range_lookback"]),
            int(p["boll_period"]),
            int(p["rsi_period"]),
            int(p["atr_period"]),
        ) + 2
        execution_seconds = max(1, int(p["execution_seconds"]))
        base_bars_per_range = max(1, int(p["range_seconds"]) // execution_seconds)
        return range_bars_needed * base_bars_per_range + 2

    def prepare(self, df: pd.DataFrame, bar: str) -> pd.DataFrame:
        p = self.params
        base_seconds = BAR_SECONDS.get(bar, int(p["execution_seconds"]))
        config = RangeStructureConfig(
            range_seconds=int(p["range_seconds"]),
            range_lookback=int(p["range_lookback"]),
            boll_period=int(p["boll_period"]),
            boll_std_mult=float(p["boll_std_mult"]),
            boll_width_atr_max=float(p["boll_width_atr_max"]),
            rsi_period=int(p["rsi_period"]),
            rsi_mid=float(p["rsi_mid"]),
            rsi_band=float(p["rsi_band"]),
            atr_period=int(p["atr_period"]),
            width_atr_max=float(p["width_atr_max"]),
        )
        out = attach_causal_range(df, base_seconds, config)
        return attach_execution_flags(
            out,
            wick_ratio=float(p["wick_ratio"]),
            touch_atr_mult=float(p["touch_atr_mult"]),
        )

    def on_candle(
        self,
        df: pd.DataFrame,
        i: int,
        ctx: BrokerContext,
    ) -> list[Signal]:
        row = df.iloc[i]
        if self._has_missing_range(row):
            return []

        if ctx.position_side == "long":
            return self._long_exit(row)
        if ctx.position_side == "short":
            return self._short_exit(row)
        if not bool(row["is_range_stable"]):
            return []
        return self._entry_signal(row)

    def _has_missing_range(self, row: pd.Series) -> bool:
        required = (
            row["range_high"],
            row["range_low"],
            row["range_mid"],
            row["range_atr"],
            row["is_range_stable"],
        )
        return any(pd.isna(value) for value in required)

    def _long_exit(self, row: pd.Series) -> list[Signal]:
        c = float(row["c"])
        h = float(row["h"])
        lo = float(row["l"])
        mid = float(row["range_mid"])
        stop = self._lower_stop(row)
        if h >= mid or c >= mid:
            return [Signal(side="close", reason="Target range mid")]
        if lo <= stop:
            return [Signal(side="close", reason="Range stop lower")]
        if not bool(row["is_range_stable"]):
            return [Signal(side="close", reason="Range invalidated")]
        return []

    def _short_exit(self, row: pd.Series) -> list[Signal]:
        c = float(row["c"])
        h = float(row["h"])
        lo = float(row["l"])
        mid = float(row["range_mid"])
        stop = self._upper_stop(row)
        if lo <= mid or c <= mid:
            return [Signal(side="close", reason="Target range mid")]
        if h >= stop:
            return [Signal(side="close", reason="Range stop upper")]
        if not bool(row["is_range_stable"]):
            return [Signal(side="close", reason="Range invalidated")]
        return []

    def _entry_signal(self, row: pd.Series) -> list[Signal]:
        if self._can_short(row):
            return [Signal(
                side="short",
                strength=float(self.params["strength"]),
                reason="Range upper short",
            )]
        if self._can_long(row):
            return [Signal(
                side="long",
                strength=float(self.params["strength"]),
                reason="Range lower long",
            )]
        return []

    def _can_short(self, row: pd.Series) -> bool:
        if not bool(row["upper_rejection"]):
            return False
        p = self.params
        atr_v = float(row["range_atr"])
        zone_low = float(row["range_high"]) - float(p["entry_zone_atr_mult"]) * atr_v
        zone_high = float(row["range_high"]) + float(p["touch_atr_mult"]) * atr_v
        touches_zone = self._any_in_zone((float(row["h"]), float(row["c"])), zone_low, zone_high)
        rejects_down = float(row["c"]) < float(row["o"]) or float(row["c"]) < float(row["range_high"])
        return touches_zone and rejects_down

    def _can_long(self, row: pd.Series) -> bool:
        if not bool(row["lower_rejection"]):
            return False
        p = self.params
        atr_v = float(row["range_atr"])
        zone_low = float(row["range_low"]) - float(p["touch_atr_mult"]) * atr_v
        zone_high = float(row["range_low"]) + float(p["entry_zone_atr_mult"]) * atr_v
        touches_zone = self._any_in_zone((float(row["l"]), float(row["c"])), zone_low, zone_high)
        rejects_up = float(row["c"]) > float(row["o"]) or float(row["c"]) > float(row["range_low"])
        return touches_zone and rejects_up

    def _lower_stop(self, row: pd.Series) -> float:
        return float(row["range_low"]) - float(self.params["stop_atr_mult"]) * float(row["range_atr"])

    def _upper_stop(self, row: pd.Series) -> float:
        return float(row["range_high"]) + float(self.params["stop_atr_mult"]) * float(row["range_atr"])

    def _any_in_zone(self, prices: tuple[float, ...], low: float, high: float) -> bool:
        return any(low <= price <= high for price in prices)
