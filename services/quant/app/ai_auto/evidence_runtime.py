"""实证准入的会话侧运行时（ai-trade-evidence-gated-capital §3.4）。

与 `ProfitLockRuntime` 同一范式：纯同步判定、不碰 executor、不触达交易所；会话只留几行薄接线。
`rule=None` ⇒ 全部方法零动作，既有直接构造会话的单测零回归。

职责：会话内未结束影子的推进与去重、按方向的桶样本（启动快照 + 会话内新结束的）、门禁裁决、
待写队列与状态 wire。持久化委托 `evidence_service.persist_rows`（延迟导入，保持本模块无 IO 依赖）。

纪律：影子与真实成交完全解耦。`note_executed` 只读 `executed["traded"]` 一个布尔。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace

from .evidence_gate import (
    BucketStats,
    EvidenceRule,
    apply_evidence_gate,
    bucket_stats,
    gating_of,
    resolve_state,
    select_samples,
    side_of_action,
    stats_wire,
)
from .open_funnel import wants_open
from .shadow_ledger import (
    STATUS_MATURED,
    STATUS_OPEN,
    STATUS_VOID,
    VOID_GAP,
    Shadow,
    advance_shadow,
    bar_seconds,
    open_shadow,
    replay_shadow,
    void_shadow,
)

logger = logging.getLogger(__name__)

SIDES = ("long", "short")
# 合并样本取桶窗口的倍数（与 `evidence_service.load_snapshots` 的 LIMIT 同一个数）。
POOLED_WINDOW_MULT = 5


@dataclass(frozen=True)
class EvidenceSnapshot:
    """启动期一次性快照（会话内不刷新合并样本，与 `track_record` 的 R-5 同一姿态）。"""

    samples: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    pooled: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    open_shadows: list[Shadow] = field(default_factory=list)
    as_of: float = 0.0
    error: bool = False


class EvidenceRuntime:
    """一个会话（单标的、单执行周期）的影子账本与实证准入闸。"""

    def __init__(self, rule: EvidenceRule | None, snapshot: EvidenceSnapshot | None, *,
                 inst_id: str, bar: str, run_id: int, group_id: str | None,
                 fallback_cost_bps: float, sessionmaker=None,
                 now: Callable[[], float] = time.time,
                 persist: Callable[..., Awaitable[None]] | None = None):
        self.rule = rule
        self.inst_id, self.bar = inst_id, bar
        # 未落库的会话（run_id=None）照样可以装配；持久化行记 0。
        self.run_id, self.group_id = int(run_id or 0), group_id or ""
        self.fallback_cost_bps = float(fallback_cost_bps)
        self._sessionmaker, self._now, self._persist = sessionmaker, now, persist
        self._snapshot_missing = snapshot is None
        snap = snapshot or EvidenceSnapshot()
        self.snapshot_error = bool(snap.error)
        self._samples = {s: deque(sorted(snap.samples.get(s, []))) for s in SIDES}
        self._pooled = {s: deque(sorted(snap.pooled.get(s, []))) for s in SIDES}
        self._takeover: list[Shadow] = list(snap.open_shadows)
        self._live: dict[str, Shadow] = {}
        self._dirty: dict[str, Shadow] = {}
        self._round_side: str | None = None
        self._counts = dict.fromkeys(
            ("intents", "shadows", "repeats", "matured", "traded", "voided", "dropped"), 0)
        self._side_counts = {s: {"shadows": 0, "traded": 0} for s in SIDES}

    @property
    def armed(self) -> bool:
        return self.rule is not None

    # ---------------- 影子：接管 / 新建 / 推进 ----------------
    def prime(self, candles: list[dict]) -> None:
        """接管上一次会话留下的未结束影子：只用已收盘根、从头重算（§3.6.3）。"""
        if not self.armed or not self._takeover:
            return
        bar_s, now = bar_seconds(self.bar), float(self._now())
        closed = [row for row in candles if int(row["ts"]) + bar_s <= now]
        first_ts = int(closed[0]["ts"]) if closed else None
        for shadow in self._takeover:
            shadow = replace(shadow, run_id=self.run_id)   # 接管 = 推进者换成本会话
            if first_ts is None or first_ts > shadow.opened_bar_ts + bar_s:
                self._settle(void_shadow(shadow, VOID_GAP))
                continue
            rows = [r for r in closed if int(r["ts"]) > shadow.opened_bar_ts]
            self._settle(replay_shadow(shadow, rows, self.rule.ref))
        self._takeover = []

    def note_intent(self, stage1, degrade: str | None, *, px: float, atr_px: float | None,
                    cost_bps: float | None, round_no: int) -> None:
        """第一阶段方向可辨的开仓意愿 ⇒ 新影子或 `repeats+1`；方向不可知的轮（low_confidence）不计。"""
        self._round_side = None
        if not self.armed or not wants_open(stage1.action, degrade):
            return
        side = side_of_action(stage1.action)
        if side is None:
            return
        self._counts["intents"] += 1
        self._round_side = side
        current = self._open_for(side)
        if current is not None:
            self._counts["repeats"] += 1
            self._track(replace(current, repeats=current.repeats + 1))
            return
        shadow = open_shadow(
            inst_id=self.inst_id, bar=self.bar, side=side, now_wall=float(self._now()), px=px,
            atr_px=atr_px, cost_bps=cost_bps, policy=self.rule.ref, run_id=self.run_id,
            round_no=round_no, fallback_cost_bps=self.fallback_cost_bps)
        if shadow.status == STATUS_VOID:
            self._round_side = None
        else:
            self._counts["shadows"] += 1
            self._side_counts[side]["shadows"] += 1
        self._settle(shadow)

    def note_executed(self, executed: dict, *, action: str | None = None) -> None:
        """本轮影子对应一次被交易所接收的真实开仓 ⇒ `traded=True`。"""
        side = self._round_side
        self._round_side = None
        if not self.armed or side is None or not executed.get("traded"):
            return
        if action is not None and side_of_action(action) != side:
            return
        current = self._open_for(side)
        if current is None or current.traded:
            return
        self._counts["traded"] += 1
        self._side_counts[side]["traded"] += 1
        self._track(replace(current, traded=True))

    def on_bar(self, row: dict) -> None:
        """确认根推进全部未结束影子；结束者进桶样本与待写队列（纯本地计算）。"""
        if not self.armed:
            return
        for shadow in list(self._live.values()):
            advanced = advance_shadow(shadow, row, self.rule.ref)
            if advanced is not shadow:
                self._settle(advanced)

    # ---------------- 门禁 ----------------
    def side_stats(self, side: str) -> BucketStats | None:
        """本方向桶统计（已做合并证伪）；快照未装配 ⇒ None（门禁按未证实处理）。"""
        if not self.armed or self._snapshot_missing:
            return None
        now = float(self._now())
        bucket = bucket_stats(select_samples(self._samples[side], self.rule, now=now),
                              self.rule)
        wide = replace(self.rule, window=self.rule.window * POOLED_WINDOW_MULT)
        pooled = bucket_stats(select_samples(self._pooled[side], wide, now=now), self.rule)
        return resolve_state(bucket, pooled, self.rule)

    def gate(self, decision, degrade: str | None, *, position_side: str):
        """返回 `(decision, degrade, wire)`；未装配 / 豁免 ⇒ 原对象直通、wire=None。"""
        if not self.armed:
            return decision, degrade, None
        side = side_of_action(decision.action)
        stats = self.side_stats(side) if side is not None else None
        out = apply_evidence_gate(decision, stats, self.rule, position_side=position_side)
        if out.lane == "exempt":
            return decision, degrade, None
        return out.decision, (degrade or out.reason), out.wire

    # ---------------- 持久化 ----------------
    async def persist_pending(self, *, group_id: str | None = None) -> None:
        """批量写待写影子；任何失败只 warning 并计入 `dropped`（下一次写入会带上完整行自愈）。

        `group_id` 由会话在写库时传入：分组号是 manager 在构造会话**之后**才赋上的。
        """
        if group_id:
            self.group_id = group_id
        if not self.armed or not self._dirty:
            return
        batch = list(self._dirty.values())
        self._dirty = {}
        try:
            persist = self._persist
            if persist is None:
                from .evidence_service import persist_rows
                persist = persist_rows
            await persist(batch, group_id=self.group_id, sessionmaker=self._sessionmaker)
        except asyncio.CancelledError:   # 停止 / 暂停路径的 2 s 上限到点：本批计入 dropped 再上抛
            self._counts["dropped"] += len(batch)
            raise
        except Exception as exc:  # noqa: BLE001 —— 证据是富化，绝不拖垮决策轮
            self._counts["dropped"] += len(batch)
            logger.warning("ai-trade evidence persist dropped %d rows (%s/%s): %s",
                           len(batch), self.inst_id, self.bar, type(exc).__name__)

    # ---------------- wire ----------------
    def stats(self) -> dict:
        return dict(self._counts)

    def status_wire(self) -> dict | None:
        """成员状态键 `evidence`（§5.2）；未装配 ⇒ None（调用方整键省略）。"""
        if not self.armed:
            return None
        sides = {}
        for side in SIDES:
            stats = self.side_stats(side)
            state = stats.state if stats is not None else "unproven"
            sides[side] = {
                "state": state, **stats_wire(stats),
                "gating": gating_of(state, self.rule.policy),
                "openShadow": self._open_for(side) is not None,
                "sessionShadows": self._side_counts[side]["shadows"],
                "sessionTraded": self._side_counts[side]["traded"]}
        return {"policy": self.rule.policy, "minSamples": self.rule.min_samples,
                "refPolicy": self.rule.ref.version(), "sides": sides,
                "session": self.stats(), "snapshotError": self.snapshot_error}

    # ---------------- 内部 ----------------
    def _open_for(self, side: str) -> Shadow | None:
        found = [s for s in self._live.values() if s.side == side and s.status == STATUS_OPEN]
        return max(found, key=lambda s: s.opened_wall) if found else None

    def _track(self, shadow: Shadow) -> None:
        self._live[shadow.shadow_id] = shadow
        self._dirty[shadow.shadow_id] = shadow

    def _settle(self, shadow: Shadow) -> None:
        """登记一笔影子的新状态：open 继续跟踪；matured 入桶；void 只计数。"""
        self._dirty[shadow.shadow_id] = shadow
        if shadow.status == STATUS_OPEN:
            self._live[shadow.shadow_id] = shadow
            return
        self._live.pop(shadow.shadow_id, None)
        if shadow.status == STATUS_MATURED and shadow.net_bps is not None:
            self._counts["matured"] += 1
            sample = (float(shadow.exit_wall or self._now()), float(shadow.net_bps))
            self._samples[shadow.side].append(sample)
            self._pooled[shadow.side].append(sample)
        else:
            self._counts["voided"] += 1
