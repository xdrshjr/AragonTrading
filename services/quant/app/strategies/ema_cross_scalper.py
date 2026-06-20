"""策略 3：双 EMA 交叉剥头皮 `ema_cross_scalper` (spec §5.2，建议 5m/15m)。

快 EMA 上穿慢 EMA 做多，下穿做空（反手）；slope_filter 过滤震荡。
多周期升级（MTF §3.7）：可选「高周期趋势过滤」——基础周期金叉仅当高周期(默认 1H)
也处于上行才放行、死叉仅当高周期下行才放行，直接对标用户的「5m 看 1H」诉求。
"""
from __future__ import annotations

import pandas as pd

from ..engine.indicators import ema
from ..engine.mtf import bar_to_seconds, causal_htf_merge
from ..engine.simulator import BrokerContext
from .base import BaseStrategy, Signal, StrategyMeta, register_strategy


@register_strategy
class EmaCrossScalper(BaseStrategy):
    meta = StrategyMeta(
        key="ema_cross_scalper",
        name="双EMA交叉剥头皮",
        description=(
            "快/慢 EMA 金叉做多、死叉做空（反手）；慢线斜率过滤震荡。高频，建议 5m/15m。"
            "可选高周期(默认1H)趋势过滤：综合大周期趋势再决定是否在基础周期入场——"
            "顺势金叉/死叉才开仓。高周期数据不足时仅无信号（除非开启 htf_required 禁开）。"
        ),
        default_params={
            "fast": 9,
            "slow": 21,
            "slope_filter": 0.0,
            "htf_bar": "1H",
            "htf_trend_filter": True,
            "htf_required": False,
            "stop_loss_pct": 0.015,
            "take_profit_pct": 0.03,
            "max_hold_bars": 36,
            "strength": 0.8,
        },
        params_schema={
            "fast": {"type": "int", "default": 9, "min": 2, "max": 50, "step": 1, "label": "快 EMA"},
            "slow": {"type": "int", "default": 21, "min": 5, "max": 200, "step": 1, "label": "慢 EMA"},
            "slope_filter": {"type": "number", "default": 0.0, "min": 0.0, "max": 0.01, "step": 0.0005, "label": "斜率过滤"},
            "htf_bar": {"type": "enum", "default": "1H", "options": ["15m", "30m", "1H", "4H", "1D"], "label": "高周期(趋势过滤)"},
            "htf_trend_filter": {"type": "bool", "default": True, "label": "启用高周期趋势过滤"},
            "htf_required": {"type": "bool", "default": False, "label": "高周期数据缺失时禁止开仓"},
            "stop_loss_pct": {"type": "number", "default": 0.015, "min": 0.003, "max": 0.1, "step": 0.001, "label": "止损%"},
            "take_profit_pct": {"type": "number", "default": 0.03, "min": 0.003, "max": 0.2, "step": 0.001, "label": "止盈%"},
            "max_hold_bars": {"type": "int", "default": 36, "min": 1, "max": 300, "step": 1, "label": "最大持仓根数"},
            "strength": {"type": "number", "default": 0.8, "min": 0.1, "max": 1.0, "step": 0.05, "label": "仓位比例"},
        },
    )

    def warmup(self) -> int:
        return int(self.params["slow"]) + 2

    def timeframes(self) -> list[str]:
        """开启高周期过滤时声明所需高周期 bar（是否严格高于基础周期由调用方过滤）。"""
        if not bool(self.params.get("htf_trend_filter", False)):
            return []
        return [str(self.params.get("htf_bar", "1H"))]

    def prepare(self, df: pd.DataFrame, bar: str) -> pd.DataFrame:
        df = df.copy()
        df["ema_fast"] = ema(df["c"], int(self.params["fast"]))
        df["ema_slow"] = ema(df["c"], int(self.params["slow"]))
        return df

    def attach_timeframes(self, df: pd.DataFrame, bar: str,
                          frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """把高周期 EMA 因果并入基础 df：数值列 `htf_<bar>_ema_*`，合并后派生布尔趋势列。"""
        if not bool(self.params.get("htf_trend_filter", False)):
            return df
        htf_bar = str(self.params.get("htf_bar", "1H"))
        htf_df = frames.get(htf_bar)
        if htf_df is None or htf_df.empty:
            return df   # 缺帧：不建列；on_candle 据 htf_required 决策（数值列 NaN 语义）
        fast = int(self.params["fast"])
        slow = int(self.params["slow"])

        def _compute(h: pd.DataFrame) -> pd.DataFrame:
            out = pd.DataFrame(index=h.index)
            out["ema_fast"] = ema(h["c"], fast)
            out["ema_slow"] = ema(h["c"], slow)
            out["slope"] = out["ema_slow"].diff()
            return out

        prefix = f"htf_{htf_bar}_"
        merged = causal_htf_merge(df, htf_df, bar_to_seconds(htf_bar),
                                  compute=_compute, prefix=prefix)
        df = df.copy()
        for col in merged.columns:
            df[col] = merged[col]
        # 【P1-1】布尔趋势列在合并之后由数值列派生（NaN 比较得 False，绝不 bool(nan)→True）。
        df[f"{prefix}uptrend"] = (df[f"{prefix}ema_fast"] > df[f"{prefix}ema_slow"]) \
            & (df[f"{prefix}slope"] > 0)
        df[f"{prefix}downtrend"] = (df[f"{prefix}ema_fast"] < df[f"{prefix}ema_slow"]) \
            & (df[f"{prefix}slope"] < 0)
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
        up, down = self._htf_gate(df, i)
        if cross_up and slope >= slope_filter and up:
            return [Signal(side="long", strength=strength, reason="EMA 金叉")]
        if cross_down and slope <= -slope_filter and down:
            return [Signal(side="short", strength=strength, reason="EMA 死叉")]
        return []

    def _htf_gate(self, df: pd.DataFrame, i: int) -> tuple[bool, bool]:
        """高周期趋势门控，返回 `(allow_long, allow_short)`（【P1-1】显式判 NaN）。

        - 未启用过滤：恒 `(True, True)`，与单周期行为一致。
        - 启用但高周期数据缺失（无帧 / 历史不足，数值列 NaN）：按 `htf_required` 决策——
          `False`→ 不门控放行；`True`→ 两侧禁开。绝不依赖 `bool(nan)` 把「无数据」误判为同向。
        - 启用且有数据：金叉仅当高周期上行放行、死叉仅当高周期下行放行。
        """
        if not bool(self.params.get("htf_trend_filter", False)):
            return True, True
        prefix = f"htf_{str(self.params.get('htf_bar', '1H'))}_"
        ema_col = f"{prefix}ema_fast"
        if ema_col not in df.columns or pd.isna(df[ema_col].iat[i]):
            allow = not bool(self.params.get("htf_required", False))
            return allow, allow
        up = bool(df[f"{prefix}uptrend"].iat[i])
        down = bool(df[f"{prefix}downtrend"].iat[i])
        return up, down
