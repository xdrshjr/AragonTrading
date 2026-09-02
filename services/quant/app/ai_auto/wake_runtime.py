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
from .flip_detector import CONFIRMED, FlipDetector
from .wake_backlog import WakeBacklog
from .wake_guard import SkipPolicy, should_skip_interval_wake, should_skip_wake
from .wake_reason import WakeFacts, WakeReadings, classify_wake, horizon_scale


def coherent_wake_floor_s(horizon_rule, *, divisor: int) -> float:
    """与目标持仓**相干**的唤醒周期下限（秒）；未装配 / `divisor <= 0` ⇒ `0.0`（= 不约束）。

    量纲失配的实测形态（loss-and-liveness-hardening §三 D3）：`wakeIntervalMinutes=3`
    × `targetHold 4–12h` ⇒ 单次持仓被问 **80–240 次**「要不要走」。每一轮都是一次让模型自主
    平仓通道（净额 −67.52 USDT）开火的机会；实测中位持仓 30 分钟 ≈ 10 轮。下限取
    `targetHoldMinSeconds / divisor`，即「一次目标持仓期内至多被问 `divisor` 次」。

    **纯函数，零 IO、不碰 settings**：抬升与 `discipline.adjustments` 回执落在 API 装配层
    （`api/ai_auto._resolve_wake_interval_s` 与 `_resolve_discipline` 的产物都住在那里，
    而纯核心模块既拿不到 `adjustments`、也不该开始知道「服务端替我改了什么」这条通道的存在）。

    Args:
        horizon_rule: 已解析的持仓周期契约；`None` = 未装配。
        divisor: 一次目标持仓期内允许的最多决策次数；**默认 0 = 只观测不改行为**。

    Returns:
        下限秒数；不约束时为 `0.0`。
    """
    if horizon_rule is None or int(divisor) <= 0:
        return 0.0
    target_min_s = float(getattr(horizon_rule, "target_hold_min_s", 0.0) or 0.0)
    if target_min_s <= 0:
        return 0.0
    return target_min_s / float(int(divisor))


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
        # ---- 唤醒调度闭环（wake-scheduler-closed-loop §2.6）：本运行时只留**纯同步**判定，
        # 异步 / 线程 / 退避一律在 `signal_preview.PreviewRunner`。----
        self._backlog = WakeBacklog()
        self._detector = FlipDetector(
            hold_s=settings.ai_trade_signal_wake_hold_s,
            min_samples=settings.ai_trade_signal_wake_min_samples)
        self._signal_flip_wakes = 0
        self._position_watch_skips = 0
        # intel-actionability 轴 A 的两个计数。**刻意不复用 `wake_skips` / `wakeDrops` 口径**：
        # 那两个的语义是「原本会发生的墙钟唤醒被抑制了多少次」，而告警是一个全新的触发源。
        self._intel_alert_wakes = 0
        self._intel_alert_drops = 0
        self._last_flip_wake_wall = 0.0
        # 最近一次判为「应跳过」的 kind。由 `should_skip` 就地记下、`note_skip` 按同一节流口径消费
        # ——分列计数因此与 `wake_skips` **同源同口径**，不会出现第二个口径（本仓库付过学费的失效模式）。
        self._last_skip_kind: str | None = None

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

    @property
    def backlog_enabled(self) -> bool:
        """轴 A 总闸；关闭 ⇒ 只计数不补跑（观测保留，行为逐字回退）。"""
        return self._settings.ai_trade_wake_backlog_enabled

    @property
    def signal_flip_enabled(self) -> bool:
        """轴 C 的**配置**总闸。

        运行期降级（克隆连续失败即整轴关闭）由 `PreviewRunner.enabled` 单独承担：它一旦关闭就
        再也产不出 `PreviewResult`，本判定链自然空转，两处不需要也**不应**各判一次。
        """
        return self._settings.ai_trade_signal_wake_enabled

    @property
    def position_watch_skips(self) -> int:
        """轴 B 省下来的轮数（与 `wake_skips` **同口径**，只是分列，便于复盘「省的是哪一类」）。"""
        return self._position_watch_skips

    @property
    def signal_flip_wakes(self) -> int:
        return self._signal_flip_wakes

    @property
    def intel_alert_wakes(self) -> int:
        """情报告警真的起了轮的次数（轴 A）。"""
        return self._intel_alert_wakes

    @property
    def intel_alert_drops(self) -> int:
        """情报告警被丢弃 / 推迟的次数（会话不在运行、行情陈旧、无信号板、过期、在飞轮）。"""
        return self._intel_alert_drops

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
            # 轴 B 分列：`wake_skips` 的口径与数值**不变**，只额外记「省下来的是哪一类」。不分列
            # 就无法区分「省了心跳轮」与「省了持仓轮」——两者的机会成本完全不同，复盘会得出错误结论。
            if self._last_skip_kind == "position_watch":
                self._position_watch_skips += 1

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
        self._backlog.note_round_start(now_wall)
        if wake.get("backlog") is None:      # 非补跑轮 ⇒ 补跑链归零（链只统计背靠背的补跑）
            self._backlog.reset_chain()

    # ---------- 分类 / 跳过 ----------
    def classify(self, readings: WakeReadings, *, now_wall: float,
                 flip: dict | None = None, intel: dict | None = None) -> dict:
        """组装 `WakeFacts` 并判定唤醒原因；返回可直接进 payload / 事件 / 轨迹的 wire。

        `flip` / `intel` 缺省 None ⇒ 对应条件不成立、wire 无该键 ⇒ 非翻转轮 / 非告警轮**逐字节
        回退**。
        """
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
            flip=flip,
            intel=intel,
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
        """本次定时唤醒是否应跳过；总闸关闭 ⇒ **逐字回退**既有 `should_skip_interval_wake`。

        跳过时就地记下 `kind`（`_last_skip_kind`），供 `note_skip` 按**同一节流口径**分列轴 B 的
        `positionWatchSkips`——判定与计数因此共用一个数，不会长出第二个口径。
        """
        if not self.reason_enabled:
            facts = self._last_facts
            if facts is None:
                return False
            return should_skip_interval_wake(
                position_side=facts.position_side, has_resting=facts.has_resting,
                bar_index=facts.bar_index, last_round_bar_index=facts.last_round_bar_index,
                px=facts.px, last_round_px=facts.last_round_px,
                threshold=self._settings.ai_trade_wake_skip_unchanged_pct)
        skip = should_skip_wake(wake, SkipPolicy(
            threshold_pct=self._settings.ai_trade_wake_skip_unchanged_pct,
            threshold_atr_mult=self._settings.ai_trade_wake_move_atr_mult,
            max_skip_streak=self._settings.ai_trade_wake_max_skip_streak,
            position_watch_enabled=self._settings.ai_trade_wake_skip_position_watch,
            position_watch_max_skip_streak=(
                self._settings.ai_trade_wake_position_watch_max_skip_streak)))
        self._last_skip_kind = wake.get("kind") if skip else None
        return skip

    # ---------- 轴 A：唤醒补跑 ----------
    def note_interval_drop(self, now_wall: float) -> bool:
        """记一次「定时唤醒到点却撞上在飞轮」；返回是否真的计了数（节流口径同 `wake_skips`）。"""
        return self._backlog.note_interval(now_wall, min_gap_s=self._wake_interval_s)

    def note_bar_drop(self, now_wall: float) -> None:
        """记一根「确认根收线却撞上在飞轮」（不节流：每根都是独立事实）。"""
        self._backlog.note_bar(now_wall)

    def backlog_wire(self) -> dict:
        """当前待办快照（供 `decision_skipped` 事件自解释；`waitedS` 以最近一次丢弃为参照）。"""
        return self._backlog.wire(self._backlog.last_dropped_wall)

    def plan_catchup(self, readings: WakeReadings, *, now_wall: float) -> dict | None:
        """轮末补跑判定；不补跑 ⇒ None（且**不留悬空待办**）。

        「输入确实变了」刻意**不新写判据**，而是翻译成既有分类器的结论：`should_skip` 为 False
        意味着 kind 不是可跳过的（`heartbeat` / 轴 B 的 `position_watch`），也就是「新根 / 挂单 /
        浮盈跨档 / 价格越阈 / 立场翻转」至少成立一条。两处各写一套判据是本仓库反复踩过的信任崩塌来源。
        """
        if not self.backlog_enabled or not self._backlog.pending:
            return None
        ceiling = self._settings.ai_trade_wake_backlog_max_chain
        if ceiling > 0 and self._backlog.chain >= ceiling:
            self._backlog.note_ceiling_hit()
            self._backlog.consume()          # 触顶即弃单，绝不让待办无限期堆着
            return None
        wake = self.classify(readings, now_wall=now_wall)
        if self.should_skip(wake):
            self._backlog.consume()          # 输入没真变 ⇒ 不补跑，也不留悬空待办
            return None
        # `note_catchup()` 必须在 `wire()` **之前**：`chain` 要如实回答「本轮是补跑链的第几轮」，
        # 先取 wire 会让第一次补跑下发 `chain: 0` —— 一个正在补跑的轮次说自己链长为 0 是无意义的
        # （spec §2.1.4 的草图顺序与它自己 §2.8 的时间线期望值矛盾，此处按后者，见 §九 D-1）。
        self._backlog.note_catchup()
        wake["backlog"] = self._backlog.wire(now_wall)
        self._backlog.consume()
        return wake

    # ---------- 轴 C：信号驱动唤醒 ----------
    def note_new_bar(self) -> None:
        """确认根落地：解锁防抖锁存（新根 = 新证据，同方向的翻转值得再看一次）。"""
        self._detector.note_new_bar()

    def plan_signal_flip(self, result, base_board: list | None, readings: WakeReadings, *,
                         now_wall: float) -> dict | None:
        """轴 C 唤醒判定：签名 → 防抖 → 最小间隔 → 锁存；任一环不成立 ⇒ None。

        **关键不变式**：本方法返回的 wake 只影响 `payload["wake"]`。传给 `_run_decision_round` 的
        `board` 仍是**确认根**的信号板，故共识门禁、铁律层、edge 门禁、仓位层全部仍以确认根为准，
        预演结果**永远只是建议性证据**。这条是轴 C 全部安全性的支点。

        `row_key` 由 `result` 带入而**不得**回头读 `session._live_row`：预演跑在线程里、期间
        `feed_candle` 随时可能写新的 `_live_row`，回头读会把「这次采样看的是哪一行」记错。

        **有副作用**：判 `confirmed` 即锁存并计数。故调用方在「起不了轮」的时刻（例如撞上在飞轮）
        必须**根本不调用**本方法，而不是调用后丢弃返回值——否则那次确认会被白白消费掉，同一次翻转
        再也不会唤醒第二次。跟踪态本身跨 tick 保留，下一 tick 续测即可。
        """
        if not self.signal_flip_enabled or result is None or not base_board:
            return None
        from .signal_preview import flip_signature    # 延迟导入：断开 preview ↔ runtime 的环
        matched = flip_signature(base_board, result.board)
        signature = None if matched is None else matched[0]
        state = self._detector.observe(signature, now_wall, row_key=result.row_key)
        if state != CONFIRMED or matched is None:
            return None
        min_gap = self._settings.ai_trade_signal_wake_min_gap_s
        if self._last_flip_wake_wall > 0 and now_wall - self._last_flip_wake_wall < min_gap:
            return None                      # 不唤醒**且不锁存**：翻转仍在跟踪，下次仍可唤醒
        flip = {**matched[1], "heldS": round(self._detector.held_s, 3),
                "samples": self._detector.samples, "barTs": result.bar_ts}
        self._detector.note_fired()
        self._last_flip_wake_wall = now_wall
        self._signal_flip_wakes += 1
        return self.classify(readings, now_wall=now_wall, flip=flip)

    # ---------- intel-actionability 轴 A：情报告警唤醒 ----------
    def plan_intel_alert(self, handoff, readings: WakeReadings, *,
                         now_wall: float) -> dict | None:
        """告警唤醒判定（镜像 `plan_signal_flip`）；`handoff` 为 None ⇒ None。

        **与 `plan_signal_flip` 的关键差别是它没有副作用**：告警的「消费」由会话侧清空
        `_pending_intel_alert` 表达，本方法只负责把 handoff 翻译成一个 `wake` wire 并计数。
        判据（会话是否在运行、行情是否陈旧、有没有信号板、是否过期、是否撞上在飞轮）全部留在
        会话侧——它们读的是会话自己的状态，搬到这里只会让 `WakeRuntime` 反向依赖会话。

        **关键不变式**：本方法返回的 wake **只影响 `payload["wake"]`**。会话随后调用的是同一个
        `_run_decision_round`，共识门禁、铁律层、edge 门禁、仓位层、反思层、离场优先级全部照跑，
        一道不绕。这条是轴 A 全部安全性的支点（逐行照抄 `plan_signal_flip` 那句同形声明）。
        """
        if handoff is None:
            return None
        self._intel_alert_wakes += 1
        return self.classify(readings, now_wall=now_wall,
                             intel=handoff.wire(now=now_wall))

    def note_intel_alert_drop(self) -> None:
        """记一次「告警到手却没能起轮」（丢弃或推迟）。**不节流**：每一次都是独立事实。"""
        self._intel_alert_drops += 1

    def stats(self, preview_stats: dict | None = None) -> dict:
        """调度层观测键（进 `status` 与 `metrics_json.aiStats.wakeScheduler`）。

        `drops` / `catchups` 都**只**读 `WakeBacklog.total_drops` / `total_catchups` 这一个源，
        故 `status.wakeDrops == aiStats.wakeScheduler.drops` 是恒等式而不是巧合（A-6）。
        """
        return {**self._backlog.stats(),
                "positionWatchSkips": self._position_watch_skips,
                "signalFlipWakes": self._signal_flip_wakes,
                # intel-actionability §3.4.4：两个新计数与既有四个并列。它们随
                # `session.py` 的 `metrics["aiStats"]["wakeScheduler"] = self._wake.stats(...)`
                # **整体赋值**天然落库——那一行是整体赋值，不是逐键手抄（评审 P1-7）。
                "intelAlertWakes": self._intel_alert_wakes,
                "intelAlertDrops": self._intel_alert_drops,
                **(preview_stats or {})}

    def status_counts(self, preview_stats: dict | None = None) -> dict:
        """WS `status` 事件的四个调度计数（与 `stats()` **同源**，只是键名按 status 的既有风格）。

        `wakeDrops` 的口径是「**到点了却撞上在飞轮**的唤醒次数」——其中一部分若不在飞也会被守卫
        跳过，故它并不等于「系统漏掉了这么多次决策」。这个口径差必须写进前端 tooltip。
        """
        stats = self.stats(preview_stats)
        return {"wakeDrops": stats["drops"], "wakeCatchups": stats["catchups"],
                "positionWatchSkips": stats["positionWatchSkips"],
                "signalFlipWakes": stats["signalFlipWakes"],
                "intelAlertWakes": stats["intelAlertWakes"],
                "intelAlertDrops": stats["intelAlertDrops"]}

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
