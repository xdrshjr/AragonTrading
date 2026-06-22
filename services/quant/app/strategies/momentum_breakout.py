"""策略 2：暴涨动量突破 `momentum_breakout` (spec §5.2)。

近 n 根累计涨幅大 + 突破窗口高点 + 放量 → 顺势追多；镜像追空。跟踪止损退出。
"""
from __future__ import annotations

import pandas as pd

from ..engine.simulator import BrokerContext
from .base import BaseStrategy, Signal, StrategyMeta, register_strategy


@register_strategy
class MomentumBreakout(BaseStrategy):
    meta = StrategyMeta(
        key="momentum_breakout",
        name="暴涨动量突破",
        description="累计涨幅大且突破近 N 根高点、成交量放大时顺势追多（镜像追空），跟踪止损保护利润。",
        default_params={
            "surge_pct": 0.03,
            "breakout_window": 20,
            "vol_mult": 1.5,
            "trail_pct": 0.02,
            "stop_loss_pct": 0.03,
            "max_hold_bars": 48,
            "strength": 0.9,
        },
        params_schema={
            "surge_pct": {"type": "number", "default": 0.03, "min": 0.005, "max": 0.3, "step": 0.005, "label": "累计涨幅阈值"},
            "breakout_window": {"type": "int", "default": 20, "min": 5, "max": 100, "step": 1, "label": "突破窗口"},
            "vol_mult": {"type": "number", "default": 1.5, "min": 1.0, "max": 5.0, "step": 0.1, "label": "放量倍数"},
            "trail_pct": {"type": "number", "default": 0.02, "min": 0.005, "max": 0.1, "step": 0.005, "label": "跟踪止损%"},
            "stop_loss_pct": {"type": "number", "default": 0.03, "min": 0.005, "max": 0.1, "step": 0.005, "label": "硬止损%"},
            "max_hold_bars": {"type": "int", "default": 48, "min": 1, "max": 300, "step": 1, "label": "最大持仓根数"},
            "strength": {"type": "number", "default": 0.9, "min": 0.1, "max": 1.0, "step": 0.05, "label": "仓位比例"},
        },
    )

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        self._reset_state()

    def _reset_state(self) -> None:
        """按笔清零跟踪止损运行态（绝不在 `prepare()` 调用，对齐 trend_pullback_long）。"""
        self._peak = 0.0            # 入场后运行最高价（多头跟踪止损基准）
        self._trough = float("inf") # 入场后运行最低价（空头跟踪止损基准）

    def warmup(self) -> int:
        return int(self.params["breakout_window"]) + 2

    def prepare(self, df: pd.DataFrame, bar: str) -> pd.DataFrame:
        df = df.copy()
        w = int(self.params["breakout_window"])
        df["prior_high"] = df["h"].shift(1).rolling(w, min_periods=w).max()
        df["prior_low"] = df["l"].shift(1).rolling(w, min_periods=w).min()
        df["vol_mean"] = df["vol"].shift(1).rolling(w, min_periods=w).mean()
        return df  # 不触碰 self._* 运行态（paper/live 逐根重调，写入会清空跟踪止损）

    def on_candle(self, df: pd.DataFrame, i: int, ctx: BrokerContext) -> list[Signal]:
        w = int(self.params["breakout_window"])
        if i < w:
            return []
        c = df["c"].iat[i]; h = df["h"].iat[i]; lo = df["l"].iat[i]; vol = df["vol"].iat[i]
        prior_high = df["prior_high"].iat[i]
        prior_low = df["prior_low"].iat[i]
        vol_mean = df["vol_mean"].iat[i]
        if pd.isna(prior_high) or pd.isna(vol_mean) or vol_mean <= 0:
            return []

        trail = float(self.params["trail_pct"])

        # 持仓中：跟踪止损
        if ctx.position_side == "long":
            self._peak = max(self._peak, h)
            if c <= self._peak * (1 - trail):
                return [Signal(side="close", reason="跟踪止损(多)")]
            return []
        if ctx.position_side == "short":
            self._trough = min(self._trough, lo)
            if c >= self._trough * (1 + trail):
                return [Signal(side="close", reason="跟踪止损(空)")]
            return []

        # 空仓：清空上一笔持仓残留的运行极值，再寻找突破入场（每笔独立，§18 D5）
        self._reset_state()
        surge_pct = float(self.params["surge_pct"])
        vol_mult = float(self.params["vol_mult"])
        strength = float(self.params["strength"])
        prev = df["c"].iat[i - w]
        surge = (c - prev) / prev if prev else 0.0

        if c > prior_high and surge >= surge_pct and vol >= vol_mult * vol_mean:
            self._peak = h
            return [Signal(side="long", strength=strength,
                           reason=f"暴涨突破(累计{surge * 100:.1f}%)")]
        if c < prior_low and surge <= -surge_pct and vol >= vol_mult * vol_mean:
            self._trough = lo
            return [Signal(side="short", strength=strength,
                           reason=f"暴跌突破(累计{surge * 100:.1f}%)")]
        return []
