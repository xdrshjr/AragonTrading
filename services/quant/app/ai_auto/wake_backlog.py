"""被丢弃唤醒的待办账本（ai-trade-wake-scheduler-closed-loop 轴 A，纯状态 + 纯函数，无 IO）。

诊断问题 ④：上一轮 LLM 决策还没跑完时，新的唤醒请求会被**静默丢弃**。一轮最坏 ≈379 秒
（4×60 决策 + 退避 + 3×45 反思），在 1m / 5m 执行周期下这会吞掉 6 根确认根；在定时唤醒被用户
关闭（`wake_interval_s = 0`）的会话里，这 6 根**永远**不会被补看。用户口中的「有时候叫得到有时候
叫不到」就是它。

本模块只记账与判定，绝不做 IO、不碰事件循环——补跑的编排（`plan_catchup`）在 `WakeRuntime`，
起轮在 `session._maybe_catchup_wake`。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WakeBacklog:
    """在飞轮期间被丢弃的唤醒请求（轴 A）。

    三条计数口径（含糊会同时污染 prompt 文案与 A-6 的恒等式，故逐字钉死）：

    1. `note_interval` 的节流基准是 `last_noted_wall`，语义是「**被抑制的、原本会发生的**唤醒次数」
       ——与 `session._count_wake_skip` 同款。裸 tick 计数会把 379 秒记成 ~75 次，而那段时间里原本
       真的会发生的定时唤醒只有 3 次（t≈120/240/360，`min_gap_s=120`）。只有后者能如实说给模型听。
    2. `note_bar` **不节流**：每一根被丢弃的确认根都是一件独立的、真实发生过的事。
    3. `total_drops` 的自增点唯一（`note_interval` 计数成功时 +1、`note_bar` 每次 +1），`consume()`
       **不清零**——`status.wakeDrops` 与 `aiStats.wakeScheduler.drops` 都只读它，两个数才不会各自漂。
    """

    dropped_intervals: int = 0     # 被丢弃的定时唤醒次数（按唤醒周期节流后的口径）
    dropped_bars: int = 0          # 被丢弃的**确认根**首次研判次数（每根真实计一次，不节流）
    first_dropped_wall: float = 0.0
    last_dropped_wall: float = 0.0
    last_noted_wall: float = 0.0   # 定时丢弃的节流基准（跨轮连续，consume 不清）
    total_drops: int = 0           # 会话累计（观测用，consume 不清零）
    total_catchups: int = 0
    ceiling_hits: int = 0
    chain: int = 0                 # 连续补跑链长度（非补跑轮启动即归零）

    @property
    def pending(self) -> bool:
        """是否有待办（`consume()` 之后恒 False）。"""
        return self.dropped_intervals > 0 or self.dropped_bars > 0

    def note_interval(self, now_wall: float, *, min_gap_s: float) -> bool:
        """记一次「定时唤醒到点却撞上在飞轮」；按 `min_gap_s` 节流。

        Args:
            now_wall: 当前墙钟。
            min_gap_s: 节流窗口（= `session.wake_interval_s`）；`<=0` 视为不节流。

        Returns:
            True = 本次真的计了数（调用方据此决定要不要补推一次 status，频率天然被节流封顶）。
        """
        throttled = now_wall - self.last_noted_wall < min_gap_s
        if min_gap_s > 0 and self.last_noted_wall > 0 and throttled:
            return False
        self.last_noted_wall = now_wall
        self.dropped_intervals += 1
        self.total_drops += 1
        self._mark_dropped(now_wall)
        return True

    def note_bar(self, now_wall: float) -> None:
        """记一根「确认根收线却撞上在飞轮」（不节流：每根都是独立事实）。"""
        self.dropped_bars += 1
        self.total_drops += 1
        self._mark_dropped(now_wall)

    def _mark_dropped(self, now_wall: float) -> None:
        if self.first_dropped_wall <= 0:
            self.first_dropped_wall = now_wall
        self.last_dropped_wall = now_wall

    def wire(self, now_wall: float) -> dict:
        """下发给模型与前端的只读对象。

        `waitedS` 的口径是 `now_wall - first_dropped_wall`（自**首次**丢弃至今等了多久），**不是**
        上一轮的研判耗时——会话没有把轮耗时记在 backlog 里，写成「上一轮研判耗时」就是在给模型一个
        它无从校验的错数。`wake_text._backlog_clause` 的措辞与此口径逐字对应。
        """
        started = self.first_dropped_wall
        waited = max(0.0, now_wall - started) if started > 0 else 0.0
        return {"droppedIntervals": self.dropped_intervals, "droppedBars": self.dropped_bars,
                "waitedS": round(waited, 3), "chain": self.chain}

    def consume(self) -> None:
        """清空待办（补跑已起 / 判定为不必补跑 / 触顶弃单三处共用）。

        只清「本次待办」四项；`last_noted_wall`（跨轮节流基准）、三个 `total_*` 与 `chain` 一律保留
        ——它们是会话级观测量，被 consume 清掉会让 A-6 的恒等式失去意义。
        """
        self.dropped_intervals = 0
        self.dropped_bars = 0
        self.first_dropped_wall = 0.0
        self.last_dropped_wall = 0.0

    def note_catchup(self) -> None:
        """补跑轮已起：链长 +1、累计补跑 +1。"""
        self.chain += 1
        self.total_catchups += 1

    def note_ceiling_hit(self) -> None:
        """补跑链触顶（触顶即弃单，绝不让待办无限期堆着）。"""
        self.ceiling_hits += 1

    def reset_chain(self) -> None:
        """非补跑轮启动即归零链长（补跑链只统计**背靠背**的补跑）。"""
        self.chain = 0

    def note_round_start(self, now_wall: float) -> None:
        """真实决策轮启动：节流基准前移，使下一段在飞期的第一次丢弃立刻能被计数。"""
        self.last_noted_wall = now_wall

    def stats(self) -> dict:
        """会话级观测（进 `status` 与 `metrics_json.aiStats.wakeScheduler`）。"""
        return {"drops": self.total_drops, "catchups": self.total_catchups,
                "catchupCeilingHits": self.ceiling_hits}
