"""策略 6：顺势回踩做多 `trend_pullback_long` (spec §3.3)。

大周期(默认 1h)EMA 定多头方向，价格回踩到 1h 支撑区(摆动低点 ± HTF-ATR)且缩量
收阳止跌时顺势做多；小级别结构破位仅收紧跟踪止损不清仓，待 2m/5m 结构也破 /
1h 方向失效 / 跟踪止损被打才全平。是 `trend_pullback_short` 的镜像（做空版）。

⚠️ 生命周期(spec §2-4)：`warmup()` 早于 `prepare()` 调用，故为 params 纯函数；
`prepare()` 在 paper/live 下逐根重复调用，故持仓运行态(`_peak/_trail_armed`)只在
`__init__` 建立、空仓分支按笔清零，**绝不在 `prepare()` 写入**。
"""
from __future__ import annotations

import pandas as pd

from ..engine.indicators import atr
from ..engine.simulator import BrokerContext
from ..okx.types import BAR_SECONDS
from . import _trend_structure as ts
from .base import BaseStrategy, Signal, StrategyMeta, register_strategy


@register_strategy
class TrendPullbackLong(BaseStrategy):
    meta = StrategyMeta(
        key="trend_pullback_long",
        name="顺势回踩做多",
        description=(
            "大周期(默认1h)EMA定多头方向，价格回踩到1h支撑区(摆动低点±HTF-ATR)且"
            "非放量、收阳止跌时顺势做多；小级别破位仅收紧跟踪止损不清仓，2m/5m结构破/"
            "1h失效/跟踪止损才全平。建议基础周期5m/15m；细周期需足够历史"
            "(如5m+1h约需600根才激活高周期EMA)，否则仅无信号而非报错。"
        ),
        default_params={
            "htf_seconds": 3600,
            "htf_ema_fast": 20,
            "htf_ema_slow": 50,
            "atr_period": 14,
            "zone_atr_mult": 1.0,
            "fast_pivot_left": 3,
            "fast_pivot_right": 2,
            "slow_pivot_left": 6,
            "slow_pivot_right": 3,
            "vol_window": 20,
            "vol_spike_mult": 2.0,
            "vol_contract_ratio": 1.0,
            "stop_atr_mult": 1.5,
            "trail_atr_mult": 2.5,
            "trail_atr_mult_tight": 1.2,
            "stop_loss_pct": 0.03,
            "max_hold_bars": 240,
            "strength": 0.8,
        },
        # 仅暴露最常调的核心项进面板(C1/R6)；pivot/vol 细参留在 default_params 可调不进面板。
        params_schema={
            "htf_seconds": {"type": "int", "default": 3600, "min": 300, "max": 14400, "step": 300, "label": "高周期秒数"},
            "htf_ema_fast": {"type": "int", "default": 20, "min": 3, "max": 100, "step": 1, "label": "高周期快EMA"},
            "htf_ema_slow": {"type": "int", "default": 50, "min": 5, "max": 200, "step": 1, "label": "高周期慢EMA"},
            "atr_period": {"type": "int", "default": 14, "min": 2, "max": 100, "step": 1, "label": "ATR周期"},
            "zone_atr_mult": {"type": "number", "default": 1.0, "min": 0.1, "max": 5.0, "step": 0.1, "label": "区域半宽(HTF-ATR)"},
            "stop_atr_mult": {"type": "number", "default": 1.5, "min": 0.2, "max": 6.0, "step": 0.1, "label": "结构止损(ATR)"},
            "trail_atr_mult": {"type": "number", "default": 2.5, "min": 0.3, "max": 8.0, "step": 0.1, "label": "跟踪止损(ATR)"},
            "trail_atr_mult_tight": {"type": "number", "default": 1.2, "min": 0.2, "max": 6.0, "step": 0.1, "label": "收紧跟踪(ATR)"},
            "stop_loss_pct": {"type": "number", "default": 0.03, "min": 0.005, "max": 0.2, "step": 0.005, "label": "兜底硬止损%"},
            "max_hold_bars": {"type": "int", "default": 240, "min": 1, "max": 1000, "step": 1, "label": "最大持仓根数(按周期换算)"},
            "strength": {"type": "number", "default": 0.8, "min": 0.1, "max": 1.0, "step": 0.05, "label": "仓位比例"},
        },
    )

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        self._reset_state()

    def _reset_state(self) -> None:
        """按笔清零持仓运行态（绝不在 `prepare()` 调用，见模块 docstring）。"""
        self._peak = 0.0           # 入场后运行最高价（跟踪止损基准）
        self._trail_armed = False  # 是否已收紧跟踪止损（假破结构修复后解除）

    def warmup(self) -> int:
        p = self.params
        return max(
            int(p["atr_period"]),
            int(p["vol_window"]) + 1,
            int(p["slow_pivot_left"]) + int(p["slow_pivot_right"]) + 1,
            int(p["fast_pivot_left"]) + int(p["fast_pivot_right"]) + 1,
        ) + 2

    def prepare(self, df: pd.DataFrame, bar: str) -> pd.DataFrame:
        df = df.copy()
        p = self.params
        base_seconds = BAR_SECONDS.get(bar, 60)
        regime = ts.attach_htf_regime(
            df, base_seconds, int(p["htf_seconds"]),
            ema_fast=int(p["htf_ema_fast"]), ema_slow=int(p["htf_ema_slow"]),
            atr_period=int(p["atr_period"]),
            pivot_left=int(p["slow_pivot_left"]), pivot_right=int(p["slow_pivot_right"]),
        )
        for col in ts.HTF_COLUMNS:
            df[col] = regime[col]
        df["atr"] = atr(df, int(p["atr_period"]))
        df["fast_swing_low"] = ts.pivot_low_confirmed(
            df["l"], int(p["fast_pivot_left"]), int(p["fast_pivot_right"]))
        df["fast_swing_high"] = ts.pivot_high_confirmed(
            df["h"], int(p["fast_pivot_left"]), int(p["fast_pivot_right"]))
        df["slow_swing_low"] = ts.pivot_low_confirmed(
            df["l"], int(p["slow_pivot_left"]), int(p["slow_pivot_right"]))
        df["slow_swing_high"] = ts.pivot_high_confirmed(
            df["h"], int(p["slow_pivot_left"]), int(p["slow_pivot_right"]))
        vol_mean, vol_contracting, vol_spike = ts.attach_volume_flags(
            df["vol"], int(p["vol_window"]),
            float(p["vol_contract_ratio"]), float(p["vol_spike_mult"]))
        df["vol_mean"] = vol_mean
        df["vol_contracting"] = vol_contracting
        df["vol_spike"] = vol_spike
        return df  # 不触碰 self._* 运行态

    def on_candle(self, df: pd.DataFrame, i: int, ctx: BrokerContext) -> list[Signal]:
        row = df.iloc[i]
        if ctx.position_side == "long":
            return self._exit_signal(row)
        if ctx.position_side != "flat":
            return []
        self._reset_state()  # 空仓第一时间按笔清零（承接 §2-4(b) 实盘正确性）
        return self._entry_signal(row)

    def _entry_signal(self, row: pd.Series) -> list[Signal]:
        """回踩 1h 支撑区止跌做多（门控 + 进区 + 非放量 + 收阳）。"""
        htf_low = row["htf_swing_low"]; htf_atr = row["htf_atr"]
        vol_mean = row["vol_mean"]
        if any(pd.isna(x) for x in (htf_low, htf_atr, vol_mean)):
            return []
        if not bool(row["htf_uptrend"]):
            return []  # 1h 破位就不用回踩买入思路
        p = self.params
        half = float(p["zone_atr_mult"]) * float(htf_atr)
        zone_low = float(htf_low) - half
        zone_high = float(htf_low) + half
        o = float(row["o"]); c = float(row["c"]); lo = float(row["l"]); h = float(row["h"])
        in_zone = (lo <= zone_high) and (c >= zone_low) and (c <= zone_high)
        if not in_zone or bool(row["vol_spike"]) or c <= o:
            return []
        self._peak = h
        return [Signal(side="long", strength=float(p["strength"]),
                       reason="回踩1h支撑区止跌")]

    def _exit_signal(self, row: pd.Series) -> list[Signal]:
        """分级收紧 / 结构 / 1h失效 / 跟踪止损离场。"""
        c = float(row["c"]); h = float(row["h"])
        self._peak = max(self._peak, h)
        atr_v = row["atr"]; fast_low = row["fast_swing_low"]
        slow_low = row["slow_swing_low"]; fast_high = row["fast_swing_high"]
        htf_low = row["htf_swing_low"]
        if any(pd.isna(x) for x in (atr_v, fast_low, slow_low, fast_high, htf_low)):
            return []
        p = self.params
        atr_v = float(atr_v)
        fast_break = c < float(fast_low)
        slow_break = c < float(slow_low)
        htf_break = (not bool(row["htf_uptrend"])) \
            or (c < float(htf_low) - float(p["stop_atr_mult"]) * atr_v)
        if fast_break:
            self._trail_armed = True            # 1min 破=收紧不清仓
        elif c > float(fast_high):
            self._trail_armed = False           # 结构站回=解除收紧(抗假破, R5)
        mult = p["trail_atr_mult_tight"] if self._trail_armed else p["trail_atr_mult"]
        trail_hit = c <= self._peak - float(mult) * atr_v
        if slow_break or htf_break or trail_hit:
            reason = "1h失效" if htf_break else ("2m/5m结构破" if slow_break else "跟踪止损")
            return [Signal(side="close", reason=f"做多离场:{reason}")]
        return []
