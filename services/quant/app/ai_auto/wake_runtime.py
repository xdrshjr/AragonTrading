"""唤醒判定 + 战术证据装配的会话级门面（ai-trade-wake-aware-context §2.6）。

`session.py` 已 1494 行、远超本仓库 800 行红线，故本特性**不往 session.py 加逻辑**，一律下沉到
`WakeRuntime`，镜像 `ProfitGuardRuntime` / `CostRuntime` / `PriorRuntime` / `SizingRuntime` 的既有
范式：纯同步，网络 IO 全在 `MarketWindow`；会话侧只留「记账 → 分类 → 跳过 → 注入」四类薄调用。

单一真源纪律（本仓库反复踩过的漂移源）：
- **战术周期集合**只在此解析一次，`MarketWindow` 经 `WindowOptions` 接收结果、不自算；
- **利润保护快照 status** 与 **ATR** 只经 `note_guard_status` / `note_atr` 一条入口，`WakeFacts` 由本
  运行时回读，调用方不得另取一次；
- **决策瞬刻 `as_of_ts`** 由会话的 `_decision_as_of_ts` 求一次，同时喂战术段与 `changeSinceLastRound`。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..config import Settings
from ..okx.types import BAR_SECONDS
from .tactical_context import (
    TacticalInputs,
    build_tactical_context,
    disclosure_tier,
    execution_disclosure,
    tactical_timeframes,
)
from .wake_guard import should_skip_interval_wake, should_skip_wake
from .wake_reason import WakeFacts, WakeReadings, classify_wake, horizon_scale


def tactical_bars_for(settings: Settings, execution_bar: str,
                      wake_interval_s: float) -> tuple[str, ...]:
    """战术周期集合的**唯一**入口（`WakeRuntime` 与 manager 的 system prompt 装配点共用）。

    manager 在建会话之前就要把周期名写进系统 prompt，若它自己再算一次，「prompt 说的」与「实际注入
    的」就会各走各的路。共用同一个函数是最便宜的同源保证。
    """
    if not settings.ai_trade_tactical_context:
        return ()
    return tactical_timeframes(execution_bar, wake_interval_s,
                               settings.ai_trade_tactical_max_bars)


def format_tactical_bars_text(bars: tuple[str, ...]) -> str:
    """系统 prompt 用的周期串（由粗到细，`"5m → 1m"`）；空集合 ⇒ 空串 ⇒ 渲染逐字节回退。"""
    return " → ".join(reversed(bars))


@dataclass(frozen=True)
class InjectionContext:
    """一次 payload 注入所需的会话读数（聚合结构体，守 CLAUDE.md「形参 ≤5」）。"""

    wake: dict
    frames: dict[str, pd.DataFrame]
    as_of_ts: int                        # 必须是 `session._decision_as_of_ts` 产物
    last_px: float
    position_side: str
    execution_primary: str | None = None


class WakeRuntime:
    """唤醒判定 + 战术证据装配的会话级门面（纯同步；网络 IO 全在 MarketWindow）。"""

    def __init__(self, execution_bar: str, wake_interval_s: float, settings: Settings):
        self._bar = execution_bar
        self._wake_interval_s = float(wake_interval_s)
        self._settings = settings
        self._tactical_bars = tactical_bars_for(settings, execution_bar, wake_interval_s)
        self._tactical_bars_text = format_tactical_bars_text(self._tactical_bars)
        self._horizon_scale = horizon_scale(self._wake_interval_s,
                                            BAR_SECONDS.get(execution_bar, 0))
        # 盯市 tick 记账（`WakeFacts` 的唯一回读来源）
        self._guard_status: str | None = None
        self._atr_px: float | None = None
        # 上一决策轮断面（None = 尚无决策轮 ⇒ 分类走 bar_advanced，首轮如实标注为「首次研判」）
        self._last_round_px: float | None = None
        self._last_round_bar_index: int | None = None
        self._last_round_as_of_ts: int | None = None
        self._last_round_guard_status: str | None = None
        self._last_round_kind: str | None = None
        self._last_facts: WakeFacts | None = None
        self._last_round_wall = 0.0       # 上一决策轮启动墙钟（只服务 sinceLastRoundS 文案）
        self._skip_streak = 0

    # ---------- 只读公开面 ----------
    @property
    def tactical_bars(self) -> tuple[str, ...]:
        return self._tactical_bars

    @property
    def tactical_bars_text(self) -> str:
        return self._tactical_bars_text

    @property
    def reason_enabled(self) -> bool:
        """唤醒原因总闸；关闭时 `wake` 整键缺席、归因不打 `wakeKind`、守卫回退既有 pct 判据。"""
        return self._settings.ai_trade_wake_reason_enabled

    @property
    def last_round_kind(self) -> str | None:
        """上一决策轮的 wake kind（供成交归因）；总闸关闭 ⇒ None（归因面整体缺席）。"""
        return self._last_round_kind if self.reason_enabled else None

    @property
    def last_reference(self) -> dict | None:
        """上一决策轮断面 `{"px","asOfTs","atrPx"}`；None = 尚无决策轮（首轮无参照）。"""
        if self._last_round_px is None or self._last_round_as_of_ts is None:
            return None
        return {"px": self._last_round_px, "asOfTs": self._last_round_as_of_ts,
                "atrPx": self._atr_px}

    @property
    def skip_streak(self) -> int:
        return self._skip_streak

    # ---------- 记账 ----------
    def note_guard_status(self, status: str | None) -> None:
        """利润保护快照 status 的**唯一**入口（盯市 tick 每次 `_refresh_profit_state` 之后即记）。

        `_maybe_profit_exit` 已在 `_maybe_interval_wake` 之前跑过，故 tick 上恒有新鲜值可读；
        `WakeFacts.guard_status` 必须由本运行时回读，调用方**不得**另取一次。
        """
        self._guard_status = status

    def note_atr(self, atr_px: float | None) -> None:
        """确认根刷新执行周期 ATR 时同步告知（`moved()` 的 ATR 判据与参照断面共用这一个数）。"""
        self._atr_px = atr_px

    def note_skip(self, counted: bool) -> None:
        """跳过计数：`counted=True`（既有 `_count_wake_skip` 的返回值）才 +1。

        口径与 `wake_skips` 完全一致（每唤醒周期至多计一次）。若按 5 秒 monitor tick 裸计数，一个
        3 分钟唤醒周期内会被打到 36，`max_skip_streak=5` 会立刻退化成「每 25 秒强制唤醒一次」——
        比不做兜底更糟。
        """
        if counted:
            self._skip_streak += 1

    def note_round_start(self, wake: dict, *, px: float, bar_index: int,
                         as_of_ts: int, now_wall: float) -> None:
        """冻结本决策轮断面（供下一轮做参照与归因），并把连续跳过计数归零。

        `as_of_ts` 是**决策瞬刻**（与本轮战术段同钟）而非 `time.time()`：混钟会让 K 线轮与定时轮
        交替时 `closedBars` 系统性偏一根。`guard_status` 从 `note_guard_status` 记下的本 tick 快照
        回读并冻结，保证「分类看到的」与「冻结的」是同一个数。
        """
        self._last_round_kind = wake.get("kind")
        self._last_round_px = px
        self._last_round_bar_index = bar_index
        self._last_round_as_of_ts = as_of_ts
        self._last_round_guard_status = self._guard_status
        self._last_round_wall = now_wall
        self._skip_streak = 0

    # ---------- 分类 / 跳过 ----------
    def classify(self, readings: WakeReadings, *, now_wall: float) -> dict:
        """组装 `WakeFacts` 并判定唤醒原因；返回可直接进 payload / 事件 / 轨迹的 wire。"""
        since = (max(0.0, now_wall - self._last_round_wall) if self._last_round_wall else 0.0)
        facts = WakeFacts(
            trigger=readings.trigger,
            position_side=readings.position_side,
            has_resting=readings.has_resting,
            bar_index=readings.bar_index,
            last_round_bar_index=self._last_round_bar_index,
            px=readings.px,
            last_round_px=self._last_round_px,
            atr_px=self._atr_px,
            guard_status=self._guard_status,
            last_round_guard_status=self._last_round_guard_status,
            since_last_round_s=since,
            resting_age_s=readings.resting_age_s,
        )
        self._last_facts = facts
        wake = classify_wake(
            facts,
            move_atr_mult=self._settings.ai_trade_wake_move_atr_mult,
            move_pct=self._settings.ai_trade_wake_skip_unchanged_pct,
            horizon_scale=self._horizon_scale)
        wake["executionBar"] = self._bar
        wake["skipStreak"] = self._skip_streak
        return wake

    def should_skip(self, wake: dict) -> bool:
        """本次定时唤醒是否应跳过；总闸关闭 ⇒ **逐字回退**既有 `should_skip_interval_wake`。"""
        if not self.reason_enabled:
            facts = self._last_facts
            if facts is None:
                return False
            return should_skip_interval_wake(
                position_side=facts.position_side, has_resting=facts.has_resting,
                bar_index=facts.bar_index, last_round_bar_index=facts.last_round_bar_index,
                px=facts.px, last_round_px=facts.last_round_px,
                threshold=self._settings.ai_trade_wake_skip_unchanged_pct)
        return should_skip_wake(
            wake,
            threshold_pct=self._settings.ai_trade_wake_skip_unchanged_pct,
            threshold_atr_mult=self._settings.ai_trade_wake_move_atr_mult,
            max_skip_streak=self._settings.ai_trade_wake_max_skip_streak)

    # ---------- payload 注入 ----------
    def inject(self, payload: dict, ctx: InjectionContext) -> None:
        """写 `payload["wake"] / ["tacticalContext"] / ["tacticalTier"] / ["contextDisclosure"]`。

        总闸关闭 ⇒ **整键缺席**（不是 None），与 baseRules / profitGuard / sizingPlan 同款范式，使
        prompt 渲染与既有 payload 冻结断言逐字节回退。三个总闸各管自己的键。
        """
        wake = ctx.wake
        if self.reason_enabled:
            payload["wake"] = wake
        context = self._build_context(ctx)
        kind = wake.get("kind") or "heartbeat"
        if context is not None:
            payload["tacticalContext"] = context
            payload["tacticalTier"] = disclosure_tier(
                wake_kind=kind, position_side=ctx.position_side, context=context)
        if self._settings.ai_trade_progressive_disclosure:
            payload["contextDisclosure"] = execution_disclosure(wake_kind=kind, enabled=True)

    def _build_context(self, ctx: InjectionContext) -> dict | None:
        """战术证据装配（契约异常按既有 MTF 同姿态降级为 None，绝不崩决策轮）。"""
        try:
            context = build_tactical_context(
                TacticalInputs(execution_bar=self._bar, tactical_bars=self._tactical_bars,
                               as_of_ts=ctx.as_of_ts, last_px=ctx.last_px,
                               execution_primary=ctx.execution_primary,
                               reference=self.last_reference),
                frames=ctx.frames, settings=self._settings)
        except ValueError:
            return None
        if context is not None:
            context["wakeIntervalS"] = self._wake_interval_s
        return context
