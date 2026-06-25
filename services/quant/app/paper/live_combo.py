"""Runtime adapter for saved combo strategies in paper sessions."""
from __future__ import annotations

import pandas as pd

from ..config import get_settings
from ..engine.backtester import run_backtest
from ..engine.combo import ComboMember, ComboPolicy
from ..engine.simulator import BrokerContext, RiskParams
from ..strategies.base import BaseStrategy, Signal, StrategyMeta


class LiveComboStrategy(BaseStrategy):
    """Active-member signal adapter for saved combo paper sessions."""

    meta = StrategyMeta(
        key="live_combo_adapter",
        name="Live Combo Adapter",
        description="Runtime-only combo paper adapter",
        kind="combo",
        default_params={},
        params_schema={},
    )

    def __init__(
        self,
        *,
        members: list[ComboMember],
        policy: ComboPolicy,
        policy_spec: dict,
        members_spec: list[dict],
    ):
        super().__init__({})
        self.members = members
        self.policy = policy
        self.policy_spec = policy_spec
        self.members_spec = members_spec
        self.active_key: str | None = None
        self.last_rebalance_i = -1
        self.bars_held = 0
        self._frames: dict[str, pd.DataFrame] = {}
        self._bar = "1H"
        self._last_ts: int | None = None
        self._runtime_i = -1

    def timeframes(self) -> list[str]:
        return sorted({tf for member in self.members for tf in member.timeframes})

    def htf_warmup_bars(self) -> int:
        return max((member.strategy.htf_warmup_bars() for member in self.members), default=0)

    def warmup(self) -> int:
        from ..engine.regime import RegimeParams

        regime = RegimeParams()
        regime_start = max(regime.ema_slow_p, regime.trend_win)
        member_warmup = max((member.strategy.warmup() for member in self.members), default=0)
        return max(member_warmup, self.policy.lookback_bars, regime_start)

    def prepare(self, df: pd.DataFrame, bar: str) -> pd.DataFrame:
        self._bar = bar
        return df

    def attach_timeframes(
        self,
        df: pd.DataFrame,
        bar: str,
        frames: dict[str, pd.DataFrame],
    ) -> pd.DataFrame:
        self._bar = bar
        self._frames = dict(frames)
        return df

    def risk_params(self) -> RiskParams:
        member = self._active_member()
        return member.strategy.risk_params() if member is not None else RiskParams()

    def on_candle(self, df: pd.DataFrame, i: int, ctx: BrokerContext) -> list[Signal]:
        if i < self.warmup():
            return []
        runtime_i = self._advance_runtime_index(df, i)
        previous_key = self.active_key
        if self._should_rebalance(runtime_i):
            self.active_key = self._choose_member(df, i)
            self.last_rebalance_i = runtime_i
        if self.active_key is None:
            return []

        switched = previous_key is not None and previous_key != self.active_key
        self.bars_held = 1 if switched or previous_key is None else self.bars_held + 1
        signals: list[Signal] = []
        if switched and ctx.position_side != "flat":
            signals.append(Signal(side="close", reason=f"combo_switch:{previous_key}->{self.active_key}"))
        signals.extend(self._member_signals(df, i, ctx))
        return signals

    def _advance_runtime_index(self, df: pd.DataFrame, i: int) -> int:
        ts = int(df.iloc[i]["ts"])
        if self._last_ts == ts:
            return self._runtime_i
        self._last_ts = ts
        self._runtime_i = i if self._runtime_i < 0 else self._runtime_i + 1
        return self._runtime_i

    def _should_rebalance(self, runtime_i: int) -> bool:
        return (
            self.active_key is None
            or runtime_i - self.last_rebalance_i >= self.policy.rebalance_bars
        )

    def _choose_member(self, df: pd.DataFrame, i: int) -> str:
        eligible = self._eligible_members(df, i)
        if not eligible:
            eligible = self.members
        if self.active_key in {member.key for member in eligible}:
            if self.bars_held < self.policy.min_dwell_bars:
                return self.active_key

        scores = {member.key: self._score_member(member, df, i) for member in eligible}
        challenger = max(eligible, key=lambda member: scores.get(member.key, 0.0))
        if self.active_key is None or self.active_key not in scores:
            return challenger.key
        incumbent = scores.get(self.active_key, 0.0)
        threshold = incumbent + abs(incumbent) * self.policy.switch_margin
        return challenger.key if scores.get(challenger.key, 0.0) > threshold else self.active_key

    def _eligible_members(self, df: pd.DataFrame, i: int) -> list[ComboMember]:
        if not self.policy.regime_gating:
            return list(self.members)
        from ..engine.regime import REGIME_STRATEGY_HINTS, RegimeParams, per_bar_primaries

        history = df.iloc[:i].copy()
        if history.empty:
            return list(self.members)
        primaries = dict(per_bar_primaries(history, RegimeParams()))
        regime = primaries.get(len(history) - 1, "range")
        hints = set(REGIME_STRATEGY_HINTS.get(regime, []))
        return [member for member in self.members if member.template_key in hints]

    def _score_member(self, member: ComboMember, df: pd.DataFrame, i: int) -> float:
        lo = max(0, i - self.policy.lookback_bars)
        window = df.iloc[lo:i].copy()
        if len(window) < max(2, member.strategy.warmup() + 2):
            return 0.0
        settings = get_settings()
        try:
            result = run_backtest(
                type(member.strategy)(dict(member.strategy.params)),
                _records(window),
                self._bar,
                initial_capital=settings.initial_capital,
                taker_fee_bps=settings.default_taker_fee_bps,
                slippage_bps=settings.default_slippage_bps,
                leverage=settings.default_leverage,
                htf_frames=self._member_htf_records(member, int(window.iloc[-1]["ts"])),
            )
        except Exception:  # noqa: BLE001 - scoring failures make the member neutral.
            return 0.0
        metrics = result.metrics
        if self.policy.selection_metric == "return":
            return float(metrics.get("totalReturn", 0.0))
        if self.policy.selection_metric == "calmar":
            drawdown = abs(float(metrics.get("maxDrawdown", 0.0)))
            return float(metrics.get("totalReturn", 0.0)) / drawdown if drawdown > 0 else 0.0
        return float(metrics.get("sharpe", 0.0))

    def _member_htf_records(self, member: ComboMember, end_ts: int) -> dict[str, list[dict]] | None:
        frames: dict[str, list[dict]] = {}
        for tf in member.timeframes:
            frame = self._frames.get(tf)
            if frame is None or frame.empty:
                continue
            frames[tf] = _records(frame[frame["ts"] <= end_ts])
        return frames or None

    def _member_signals(self, df: pd.DataFrame, i: int, ctx: BrokerContext) -> list[Signal]:
        member = self._active_member()
        if member is None or i < member.strategy.warmup():
            return []
        member_df = member.strategy.prepare(df.copy(), self._bar)
        member_frames = {
            tf: frame for tf, frame in self._frames.items() if tf in member.timeframes
        }
        if member_frames:
            member_df = member.strategy.attach_timeframes(member_df, self._bar, member_frames)
        raw = member.strategy.on_candle(member_df, i, ctx)
        return [_prefix_signal(signal, member.name) for signal in raw]

    def _active_member(self) -> ComboMember | None:
        if self.active_key is None:
            return None
        return next((member for member in self.members if member.key == self.active_key), None)


def _records(df: pd.DataFrame) -> list[dict]:
    return [
        {
            "ts": int(row.ts),
            "o": float(row.o),
            "h": float(row.h),
            "l": float(row.l),
            "c": float(row.c),
            "vol": float(row.vol),
        }
        for row in df.itertuples(index=False)
    ]


def _prefix_signal(signal: Signal, member_name: str) -> Signal:
    reason = signal.reason or "signal"
    update = {"reason": f"combo:{member_name} / {reason}"}
    if hasattr(signal, "model_copy"):
        return signal.model_copy(update=update)
    return Signal(side=signal.side, strength=signal.strength, reason=update["reason"])
