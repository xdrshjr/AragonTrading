"""持仓周期铁律层的**会话运行时**（ai-trade-holding-horizon-modes §2.4）。

镜像 `profit_guard_runtime.ProfitGuardRuntime` / `cost_runtime.CostRuntime` / `sizing_runtime.
SizingRuntime` 的既有姿态：**纯同步、无 await、不持有 session 引用、不碰 executor 写路径**。
`session.py` 已 1664 行（远超 CLAUDE.md 的 800 行红线），本特性承诺净增 ≤55 行，故一切状态推进、
计数与快照装配都落在这里，会话侧只留三个薄方法（`_horizon_facts` / `_apply_horizon_gate` /
`_emit_horizon_trace`）。

**epoch 身份是 `(side, seq)`**，与利润保护层完全同源（`profit_guard.advance_state`）。`opened_wall`
的首选真源是 `_dispatch` 里的开仓成交事件（`note_open`，精确到成交时刻）；`refresh()` 发现有持仓却
无 epoch 时兜底建一个并置 `opened_wall_estimated=True`，此时 G1 走 `epoch_estimated` 豁免。
开仓**部分成交**不重置 epoch（`_dispatch` 只在 `side=="close"` 时自增 `_position_seq`），故 `opened_wall`
必须由 `note_open` 在 epoch 首次建立时锁定，**绝不可回读 `entry_bar` 反推**（`_adopt_fill` 会在每次
增量采纳时刷新它）。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace

from .hold_horizon import (
    REASON_EXIT_UNCONFIRMED,
    REASON_HYSTERESIS,
    REASON_MIN_HOLD,
    STATUS_FLAT,
    HorizonFacts,
    HorizonRule,
    HorizonState,
    build_snapshot,
    evaluate_horizon_gate,
    gated_kind,
)
from .profit_guard_runtime import exchange_oco_px

# 被本层拦下时按前缀归类到四个计数器之一（前缀是稳定契约，与 `DEGRADE_REASON_LABELS` 同源）。
_VETO_COUNTER_KEY = {
    REASON_MIN_HOLD: "min_hold",
    REASON_EXIT_UNCONFIRMED: "unconfirmed",
    REASON_HYSTERESIS: "hysteresis",
}


def build_horizon_facts(executor, readings, *, stale_feed: bool,
                        cost_bps: float | None) -> HorizonFacts:
    """把 executor 的持仓读数 + 会话时钟组装成一次判定所需的断面（镜像 `build_position_facts`）。

    判定价恒取 `readings.mark_px`（`_current_px()`），与利润保护层**同源**——两层各取一次价必然出现
    「UI 上的退出线不是判定用的那个数」（[R-5]）。交易所侧 OCO 只**只读转发** `exchange_oco_px`
    的既有产物，绝不重算（§2.8 第 3 条）。

    Args:
        executor: 本会话的 `LiveExecutor`（只读 `projected` / `entry_price` / `risk` / `trades`）。
        readings: `SessionReadings`（seq / 墙钟 / 判定价 / 根索引 / 判定价年龄）。
        stale_feed: 会话级行情降级标志（`_stale_degraded`）。
        cost_bps: 按**持仓方向**估的往返成本（`CostRuntime.estimate_cost_for_position`，[P1-2]）。
    """
    tp_px, sl_px, _ = exchange_oco_px(executor, executor.projected)
    return HorizonFacts(
        seq=readings.seq, side=executor.projected, entry_px=executor.entry_price,
        mark_px=readings.mark_px, leverage=executor.leverage, now_wall=readings.now_wall,
        px_age_s=readings.px_age_s, stale_feed=stale_feed,
        bars_in_position=((readings.bar_index - executor.entry_bar)
                          if executor.entry_bar >= 0 else None),
        max_hold_bars=executor.risk.max_hold_bars,
        stop_loss_pct=executor.risk.stop_loss_pct, cost_bps=cost_bps,
        exchange_sl_px=sl_px, exchange_tp_px=tp_px)


class HorizonRuntime:
    """一个会话的持仓周期层运行时（纯同步；无 IO、无 await、不碰 executor）。

    Attributes:
        rule: 已装配契约；`None` = 本会话不装配（全部方法退化为零动作、payload 整键缺席）。
        state: 当前 epoch 作用域状态（无仓 ⇒ 空 `HorizonState`）。
        last_snapshot: 最近一次 `refresh()` 产物（供 status 事件与面板 chip 在无决策轮时也能更新）。
        released_streak: 连续「本层已解除」的持仓轮数（达阈追发一条 error 事件，§4.4）。
    """

    def __init__(self, rule: HorizonRule | None) -> None:
        self.rule = rule
        self.state = HorizonState()
        self.last_snapshot: dict | None = None
        # 最近一次 `gate()` 的**本层**降级码（`None` = 本轮本层放行）。刻意与合成后的 `degrade` 分开：
        # 后者遵循「先执行者胜出」，基础策略先拦一轮就会把本层的 reason 吞掉，轨迹据它渲染必说谎。
        self.last_reason: str | None = None
        self.released_streak = 0
        self.vetoes: Counter = Counter()          # min_hold / unconfirmed / hysteresis / reverse
        self.released_rounds = 0
        self.release_reasons: Counter = Counter()
        self.confirm_window_releases = 0
        self.round_trips = 0
        self.estimated_epochs = 0
        self.hold_samples: list[float] = []

    # ---------- 状态推进 / 快照 ----------
    def refresh(self, facts: HorizonFacts) -> dict | None:
        """推进 epoch 并组装 wire 快照（**纯同步、幂等**）；未装配 → `None`。

        每个盯市 tick 与每个决策轮各调用一次。[P1-7] 双快照契约：payload 段用 round-start 的那一次
        产物，判定用 `_apply_horizon_gate` 内**重新** refresh 的那一次——两次之间只有时间向前流逝，
        故 `hold_s` 只增不减（G1 只会更松）、`confirm_window` 只会更接近到期、`loss_release` 只会
        更早生效，方向安全。
        """
        if self.rule is None:
            return None
        if facts.side not in ("long", "short"):
            self.state = HorizonState()
            self.last_snapshot = build_snapshot(self.rule, self.state, facts)
            return self.last_snapshot
        epoch = (facts.side, int(facts.seq))
        if self.state.epoch != epoch:
            # 未观察到开仓成交事件（重连 / 外部干预 / 单测直接构造）⇒ 兜底建 epoch 并如实标估算。
            self.state = HorizonState(epoch=epoch, opened_wall=float(facts.now_wall),
                                      opened_wall_estimated=True)
        self.last_snapshot = build_snapshot(self.rule, self.state, facts)
        return self.last_snapshot

    def note_intent(self, stage1_action: str, stage1_degrade: str | None, *,
                    position_side: str, now_wall: float) -> None:
        """第一阶段意图记账（连续确认计数）；**须在任何门禁之前**调用（§2.5 硬约束 4）。

        以模型的**真实意愿**为准（`decider.decide` 的直接返回值），与 `_update_hold_streak` 同源：
        若记门禁后的动作，一次 MA 通道否决就会把 close 意图的连续性洗掉，G2 永远数不到 2。
        降级轮（llm_error / timeout / low_confidence）既非真实意愿也非真实观望 ⇒ 计数**不变**。
        """
        if self.rule is None:
            return
        if gated_kind(stage1_action, position_side, gate_reverse=self.rule.gate_reverse):
            first = self.state.first_exit_intent_wall
            self.state = replace(
                self.state, exit_confirm_streak=self.state.exit_confirm_streak + 1,
                first_exit_intent_wall=float(now_wall) if first is None else first)
            return
        if stage1_degrade is not None:
            return
        self._reset_intent()

    def _reset_intent(self) -> None:
        """离场意图链条终结：streak 与窗口起点一并清空（缺后者 ⇒ 窗口在下一次意图时已经「到期」）。"""
        self.state = replace(self.state, exit_confirm_streak=0, first_exit_intent_wall=None)

    # ---------- 决策链薄封装 ----------
    def gate(self, decision, degrade: str | None,
             facts: HorizonFacts) -> tuple[object, str | None, dict | None]:
        """采断面 → 推状态 → 判定（[P1-7]：判定与下发恒取**本次** refresh 的快照）。

        未装配 / 非受控动作 ⇒ 返回**同一个** decision 对象（`is` 相等）、`degrade` 原样透传。

        Returns:
            `(decision, degrade, snapshot)`——`snapshot` 为本次判定所用的 wire（未装配 ⇒ `None`）。
        """
        self.last_reason = None
        if self.rule is None:
            return decision, degrade, None
        snapshot = self.refresh(facts)
        kind = gated_kind(decision.action, facts.side, gate_reverse=self.rule.gate_reverse)
        self._note_release(snapshot, gated=kind is not None)
        if kind is None:
            return decision, degrade, snapshot
        gated, reason = evaluate_horizon_gate(decision, snapshot, self.rule,
                                              position_side=facts.side)
        if reason is None:
            self._note_pass(snapshot)
            return decision, degrade, snapshot
        self._note_veto(reason, kind)
        self.last_reason = reason
        return gated, (degrade or reason), snapshot

    def _note_release(self, snapshot: dict | None, *, gated: bool) -> None:
        """豁免计数 + 连续解除计数（前者只记**受约束轮**以免稀释分母；后者按持仓轮计，供 §4.4 告警）。"""
        if not snapshot or snapshot.get("status") == STATUS_FLAT:
            self.released_streak = 0
            return
        if not snapshot.get("released"):
            self.released_streak = 0
            return
        self.released_streak += 1
        if gated:
            self.released_rounds += 1
            self.release_reasons[str(snapshot.get("releaseReason"))] += 1

    def _note_pass(self, snapshot: dict | None) -> None:
        """放行：拦截连击归零；若是被墙钟上界放行的，单独计一笔（该阀门是否真的在用，事后要能核对）。"""
        self.state = replace(self.state, blocked_streak=0)
        if not snapshot or not snapshot.get("confirmWindowElapsed"):
            return
        rounds = int(snapshot.get("exitConfirmRounds") or 1)
        if int(snapshot.get("exitConfirmStreak") or 0) < rounds:
            self.confirm_window_releases += 1

    def _note_veto(self, reason: str, kind: str) -> None:
        """否决计数：三道闸各一个 + 反手单独一个（反手被否是**整轮降级**，与平仓被否不可换算）。"""
        for prefix, key in _VETO_COUNTER_KEY.items():
            if reason.startswith(prefix):
                self.vetoes[key] += 1
                break
        if kind == "reverse":
            self.vetoes["reverse"] += 1
        self.state = replace(self.state, blocked_streak=self.state.blocked_streak + 1)

    # ---------- payload 注入 ----------
    def inject(self, payload: dict, snapshot: dict | None) -> None:
        """把本轮快照挂进决策 payload（未装配 ⇒ **整键缺席** ⇒ prompt 渲染逐字节回退）。

        顺带给已存在的 `profitGuard` 段打一个 `horizonArmed` 标记：利润保护宪章与平仓回执的两处
        抢跑偏置文案据它切换（§2.9 a/b）。**只在本层装配时打**，故未装配会话逐字节回退。
        """
        if self.rule is None or snapshot is None:
            return
        payload["tradingHorizon"] = snapshot
        guard = payload.get("profitGuard")
        if isinstance(guard, dict):
            guard["horizonArmed"] = True

    # ---------- 成交事件收口（`_dispatch` 是全部 executor 事件的唯一收口）----------
    def note_open(self, trade: dict, now_wall: float, *, seq: int) -> None:
        """开仓成交：锁定本 epoch 的 `opened_wall`（**部分成交不重置**——`(side, seq)` 未变即同一 epoch）。

        **估算态必须被升级**：`refresh()` 可能先于本事件观察到持仓（盯市 tick 与决策轮在不同任务里
        推进，`projected` 在成交事件扇出前就已翻面），从而兜底建出一个 `opened_wall_estimated=True`
        的 epoch。若此时按「同一 epoch 即早退」处理，估算标记会在整个持仓期内挂着 ⇒ `epoch_estimated`
        豁免每轮命中 ⇒ **G1 恒不生效且不报任何错**（与回测侧 I-5 完全同款的静默失效）。故只有已经
        由成交事件锁定过（`estimated=False`）的 epoch 才早退，其余一律以成交时刻改写。
        """
        if self.rule is None:
            return
        side = trade.get("side")
        if side not in ("long", "short"):
            return
        epoch = (side, int(seq))
        if self.state.epoch == epoch and not self.state.opened_wall_estimated:
            return
        self.state = HorizonState(epoch=epoch, opened_wall=float(now_wall),
                                  opened_wall_estimated=False)

    def note_close(self, trade: dict, now_wall: float) -> None:
        """平仓成交：记一笔真实持仓时长样本并清空 epoch（估算起点的样本**不入统计**）。

        平仓侧不存在部分结算（`_adopt_partial` 对 close 意图刻意不做增量采纳），故一个 epoch 恰对应
        一次 `note_close`。`trade` 形参保留以对齐 `_cost.record_close` / `_sizing.note_close` 的同款
        收口签名（本层只需时钟，不读成交字段）。
        """
        if self.rule is None or self.state.epoch is None:
            return
        self.round_trips += 1
        if self.state.opened_wall_estimated:
            self.estimated_epochs += 1
        else:
            self.hold_samples.append(max(0.0, float(now_wall) - float(self.state.opened_wall)))
        self.state = HorizonState()

    # ---------- 统计（§2.12）----------
    def merge_into(self, ai_stats: dict) -> dict:
        """把 `horizon` 加法键并入既有 `aiStats`（键冲突即抛，绝不静默覆盖；镜像 `merge_cost_stats`）。"""
        snapshot = self.stats()
        if snapshot is None:
            return ai_stats
        merged = dict(ai_stats or {})
        if "horizon" in merged:
            raise ValueError("horizon stats key clash with existing aiStats: horizon")
        merged["horizon"] = snapshot
        return merged

    def stats(self) -> dict | None:
        """`aiStats.horizon` 的唯一装配点；未装配 ⇒ `None`（整键缺席）。

        [P2-3] **零样本口径**：`roundTrips == 0` 时全部样本派生量下发 `None` 而非 `0`——「目标区间内
        占比 0%」与「还没有样本」在看板上是两个截然不同的结论，用 0 表示后者会把一个正常运行的会话
        读成彻底失效。
        """
        if self.rule is None:
            return None
        return {"profileId": self.rule.profile_id, "title": self.rule.title,
                "minHoldSeconds": self.rule.min_hold_s,
                "exitConfirmRounds": self.rule.exit_confirm_rounds,
                "turnoverHysteresisK": self.rule.hysteresis_k,
                "expectedHoldBars": self.rule.expected_hold_bars,
                "minHoldVetoes": self.vetoes["min_hold"],
                "unconfirmedVetoes": self.vetoes["unconfirmed"],
                "hysteresisVetoes": self.vetoes["hysteresis"],
                "reverseVetoes": self.vetoes["reverse"],
                "releasedRounds": self.released_rounds,
                "releaseReasons": dict(self.release_reasons),
                "confirmWindowReleases": self.confirm_window_releases,
                "roundTrips": self.round_trips,
                "estimatedEpochs": self.estimated_epochs,
                **self._sample_stats()}

    def _sample_stats(self) -> dict:
        """持仓时长样本的派生量；无样本 ⇒ 六键全 `None`（[P2-3]，绝不用 0 冒充「还没有样本」）。"""
        samples = sorted(self.hold_samples)
        if not samples:
            return {"avgHoldSeconds": None, "medianHoldSeconds": None,
                    "minHoldObservedSeconds": None, "maxHoldObservedSeconds": None,
                    "withinTargetRate": None, "turnoverPerDay": None}
        total = sum(samples)
        mid = len(samples) // 2
        median = (samples[mid] if len(samples) % 2
                  else (samples[mid - 1] + samples[mid]) / 2.0)
        return {"avgHoldSeconds": round(total / len(samples), 3),
                "medianHoldSeconds": round(median, 3),
                "minHoldObservedSeconds": round(samples[0], 3),
                "maxHoldObservedSeconds": round(samples[-1], 3),
                "withinTargetRate": self._within_target_rate(samples),
                "turnoverPerDay": self._turnover_per_day(samples, total)}

    def _within_target_rate(self, samples: list[float]) -> float | None:
        """落在 `[targetMin, targetMax]` 内的样本占比；未设目标区间 ⇒ `None`（没有靶子就没有命中率）。"""
        rule = self.rule
        if rule is None or rule.target_hold_max_s <= 0:
            return None
        hits = sum(1 for s in samples if rule.target_hold_min_s <= s <= rule.target_hold_max_s)
        return round(hits / len(samples), 4)

    @staticmethod
    def _turnover_per_day(samples: list[float], total: float) -> float | None:
        """日均换手次数 = 样本数 ÷（在场总时长折算的天数）；在场时长为 0 ⇒ `None`（不除零、不假数据）。

        分母刻意取**在场时长**而非会话墙钟时长：空仓等待期不该稀释「持仓期内换了多少次手」这个读数，
        而会话墙钟口径会让一个刚启动 10 分钟就完成一次往返的会话显示出 144 次/天的荒谬值。
        """
        days = total / 86400.0
        return None if days <= 0 else round(len(samples) / days, 4)
