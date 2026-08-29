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

from .exit_attribution import ExitLedger
from .gate_veto import build_gate_veto_note as build_generic_gate_veto_note
from .exit_intent import RELEASE_EXIT_PENDING, advance_intent, contradicts_exit
from .exit_priority import (
    CONVICTION_FINAL,
    LEVEL_ADJUDICATED,
    LEVEL_EXPEDITE,
    LEVEL_OVERRIDE,
)
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
from .exchange_backstop import apply_backstop_gate
from .profit_guard_runtime import exchange_oco_px
from .turnover_discipline import (
    TurnoverRule,
    apply_turnover_gate,
    build_wire as build_turnover_wire,
    bump_wire_vetoes,
    gate_skip_reason,
    init_state as init_turnover_state,
    note_veto as note_turnover_veto,
    on_close_fill,
    on_open_fill,
    refill,
)

# 被本层拦下时按前缀归类到四个计数器之一（前缀是稳定契约，与 `DEGRADE_REASON_LABELS` 同源）。
_VETO_COUNTER_KEY = {
    REASON_MIN_HOLD: "min_hold",
    REASON_EXIT_UNCONFIRMED: "unconfirmed",
    REASON_HYSTERESIS: "hysteresis",
}


def build_gate_veto_note(proposed, reason: str | None, priority=None) -> dict | None:
    """把本轮**持仓周期层**的否决压成记忆注记（exit-priority F-2）；未被否决 ⇒ `None`。

    被 G2 拦下的那一轮，`decision.action` 已经是 `"hold"`、`decision.reason` 已被
    `hold_horizon._veto` 改写成降级码，于是模型下一轮读到的自己的历史是「上轮我决定观望」。
    反思否决轮早有对等注记（`build_reflection_note`），**门禁否决轮没有**——F-1 断的是系统侧的
    计数，F-2 断的是模型侧的论证连续性，两者互为因果，共同构成那条没有出口的活锁。

    ⚠️ **本函数的三参签名保留、行为逐字不变，实现已委托给 `gate_veto.build_gate_veto_note`**
    （directional-symmetry 轴 B）：那里把同一件事泛化到了八道确定性门禁，而层名的判定只有
    `open_funnel.classify_block` 一份真源。`last_reason` 只可能是本层的三个码
    （`horizon_min_hold` / `horizon_exit_unconfirmed` / `horizon_turnover_hysteresis`），
    三者经 `classify_block` 全部归到 `"horizon"`，故委托后产物逐字节相同。

    Args:
        proposed: 进入 `_apply_horizon_gate` **那一刻**的 decision 引用（【R:P2-7】：不取更早的
            `stage1_decision`——今天基础策略层不拦 close 故两者相同，但那是隐式依赖，将来任一层
            开始改写 close 的 `reason`，注记就会静默失真）。
        reason: 本层降级码（`None` ⇒ 本轮没被本层拦下 ⇒ 返回 `None`）。
        priority: 本轮 `ExitPriority`（可为 `None`）——声明的确信度与账本进度一并写进注记，
            让模型看得见「我声明了 final 但因为 X 被按 high 处理」。

    Returns:
        注记 dict 或 `None`。
    """
    return build_generic_gate_veto_note(proposed, reason, priority=priority)


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

    def __init__(self, rule: HorizonRule | None, *, net_mode: bool = True,
                 record_exits: bool = True, turnover_rule: TurnoverRule | None = None,
                 backstop=None, repair=None) -> None:
        """
        Args:
            rule: 已装配周期契约；`None` = 本会话不装配。
            net_mode: 退出来源账本的净额还原口径。
            record_exits: 回测侧传 `False`（那边账本由 runner 自持，见下）。
            turnover_rule: 开仓侧换手纪律契约（turnover-budget §2.2.2）；**默认 `None` ⇒ 整层
                空转** ⇒ 既有直接构造 `HorizonRuntime(rule)` 的单测逐字零回归。
            backstop: 交易所侧兜底核实运行时（鸭子类型：只要求 `.wire(now_wall) -> dict | None`）。
                回测**不装配**（回测没有交易所，声称「已向交易所核实」是假话，D-5）。
                本类不持有 settings、不发任何请求——IO 全在 `exchange_backstop` 那一侧。
            repair: 交易所侧兜底自愈运行时（鸭子类型：只要求 `.wire() -> dict | None`）。
                回测同样**不装配**（让回测显示一个「已补挂」的绿勾，是本特性链要根除的那类谎言）。
        """
        self.rule = rule
        # 退出来源账本（horizon-acceptance 轴 B）：**恒构造，与 `rule` 是否装配无关**——关掉周期
        # 模式跑一段对照组正是 L1 观察窗口最需要做的事，若它随 `rule is None` 一起消失，对照臂
        # 就会「记下了数据却一个字都看不到」（[P0-4]）。
        # `record_exits=False` 只给**回测**用：那边的平仓成交由 `SimBroker` 产出、来源要靠成交
        # `reason` 与打标槽合判，故由 runner 自己持有一份账本并在**不受周期模式门控**的钩子里记
        # （挂在这里会被 `_track_horizon_epoch` 的 `rule is None` 早退连坐）。
        self.record_exits = bool(record_exits)
        self.exits = ExitLedger(net_mode=bool(net_mode))
        self.state = HorizonState()
        self.last_snapshot: dict | None = None
        # 最近一次 `gate()` 的**本层**降级码（`None` = 本轮本层放行）。刻意与合成后的 `degrade` 分开：
        # 后者遵循「先执行者胜出」，基础策略先拦一轮就会把本层的 reason 吞掉，轨迹据它渲染必说谎。
        self.last_reason: str | None = None
        # 最近一次 `gate()` 的**挂起**门禁码（`None` = 本轮未挂起）。刻意与 `last_reason` 分开：
        # 挂起不是降级，混进去会让闸计数把一次成功离场记成一次被拦（§5.5）。
        self.last_pending_reason: str | None = None
        # 离场优先级计数（exit-priority §5.4）：`override` 与 `adjudicated` 共用同一份配额。
        self.exit_levels: Counter = Counter()
        self.exit_overrides_used = 0
        self.exit_final_declared = 0
        self.exit_final_downgraded = 0
        self.exit_fail_open_rounds = 0
        self.released_streak = 0
        self.vetoes: Counter = Counter()          # min_hold / unconfirmed / hysteresis / reverse
        self.released_rounds = 0
        self.release_reasons: Counter = Counter()
        self.confirm_window_releases = 0
        self.round_trips = 0
        self.estimated_epochs = 0
        self.hold_samples: list[float] = []
        # ---- 开仓侧换手纪律（turnover-budget §2.2）----
        # `turnover_state` 是**会话级**、跨 epoch 存活的（R-5：挂进 `HorizonState` 会在每笔平仓
        # 被清空，G5 就变成一个永远满桶的摆设）。`last_refill_wall` 刻意从 0 起：首次读取时
        # elapsed 极大、但令牌本就已是满桶，夹到容量后逐字等价于「在那一刻装配」——本类因此
        # 不需要持有任何时钟（实盘墙钟与回测 bar ts 都能直接喂进来）。
        self.turnover_rule = turnover_rule
        self.turnover_state = init_turnover_state(turnover_rule, now_wall=0.0)
        self.last_turnover_reason: str | None = None
        self._cooldown_was_active = False
        self.backstop = backstop
        self.repair = repair
        self.last_backstop_reason: str | None = None
        self.backstop_vetoes = 0

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
            self.last_snapshot = self._snapshot(facts)
            return self.last_snapshot
        epoch = (facts.side, int(facts.seq))
        if self.state.epoch != epoch:
            # 未观察到开仓成交事件（重连 / 外部干预 / 单测直接构造）⇒ 兜底建 epoch 并如实标估算。
            self.state = HorizonState(epoch=epoch, opened_wall=float(facts.now_wall),
                                      opened_wall_estimated=True)
        self.last_snapshot = self._snapshot(facts)
        return self.last_snapshot

    def _snapshot(self, facts: HorizonFacts) -> dict:
        """装配快照并挂上两个新嵌套键（turnover-budget §5.4）。

        **空仓分支同样带 `turnover`**（[P0-3]）：`flat` 恰是 G4/G5 唯一真正会拦人的状态，若只在
        持仓分支挂，文案层就只能沿用那句「当前空仓：本层不约束开仓」——系统会每轮主动说反话。
        两键在未装配时**整键缺席**（不是 `null`），prompt 因此逐字节回退。
        """
        now_wall = float(facts.now_wall)
        self.turnover_state = refill(self.turnover_state, self.turnover_rule,
                                     now_wall=now_wall)
        return build_snapshot(
            self.rule, self.state, facts,
            turnover=build_turnover_wire(self.turnover_state, self.turnover_rule,
                                         now_wall=now_wall),
            backstop=self._backstop_wire(now_wall))

    def _backstop_wire(self, now_wall: float) -> dict | None:
        """检测层 wire + 自愈层的**嵌套加法子键**（backstop-repair §4.4 / 上一轮 D-10）。

        `repair` 嵌在既有整对象透传的 `backstop` 里，故 `store.ts` 的逐键手抄 reducer 一行都不必动
        （上一轮 [P0-1] 正是踩在那个坑上）。两层各自未装配时**整键缺席**（不是 `null`），
        prompt 与运行卡因此逐字节回退。
        """
        if self.backstop is None:
            return None
        wire = self.backstop.wire(now_wall)
        repair = None if self.repair is None else self.repair.wire()
        return wire if wire is None or repair is None else {**wire, "repair": repair}

    def note_intent(self, stage1_action: str, stage1_degrade: str | None, *,
                    position_side: str, now_wall: float) -> None:
        """第一阶段意图记账（**带衰减的跨轮账本**，exit-priority 轴 A）；须在任何门禁之前调用。

        以模型的**真实意愿**为准（`decider.decide` 的直接返回值），与 `_update_hold_streak` 同源：
        若记门禁后的动作，一次 MA 通道否决就会把 close 意图的连续性洗掉，G2 永远数不到 2。

        判定表全部下沉纯核心 `exit_intent.advance_intent`（本类只做状态推进，不判断）。
        `HorizonRule.exit_intent_decay_rounds` 默认 **0** ⇒ 逐字回退今天的「一轮非离场即清零」。
        """
        if self.rule is None:
            return
        gated = gated_kind(stage1_action, position_side,
                           gate_reverse=self.rule.gate_reverse) is not None
        self.state = advance_intent(
            self.state, gated=gated,
            contradicts=contradicts_exit(stage1_action, position_side),
            degraded=stage1_degrade is not None, now_wall=float(now_wall),
            decay_rounds=int(self.rule.exit_intent_decay_rounds))

    # ---------- 决策链薄封装 ----------
    def gate(self, decision, degrade: str | None, facts: HorizonFacts, *,
             priority=None) -> tuple[object, str | None, dict | None, bool]:
        """采断面 → 推状态 → 判定（[P1-7]：判定与下发恒取**本次** refresh 的快照）。

        未装配 / 非受控动作 ⇒ 返回**同一个** decision 对象（`is` 相等）、`degrade` 原样透传。

        Args:
            decision: 本轮决策（已过基础策略层）。
            degrade: 既有合成降级码（「先执行者胜出」，本层只在其为空时写入）。
            facts: 本轮持仓周期断面。
            priority: 本轮 `ExitPriority`（exit-priority §4.3.3）；`None` ⇒ 不解闸、不挂起
                ⇒ **逐字回退**到本特性合入前。

        Returns:
            `(decision, degrade, snapshot, pending)`——`pending` 为真表示本轮**挂起待裁决**：
            decision 原样继续下行，且本层**不 veto、不写 `degrade`、不计 `_note_veto`、不置
            `last_reason`**（【R:P1-3】：否则 `_discipline.note_round(degrade)` 会把一次成功
            离场记成一次被拦，`_emit_degraded` 还会推一条降级事件）。「差什么」只写进
            `last_pending_reason` → `exitPriority.reason` 与 trace。
        """
        self.last_reason = None
        self.last_pending_reason = None
        if self.rule is None:
            return decision, degrade, None, False
        snapshot = self.refresh(facts)
        kind = gated_kind(decision.action, facts.side, gate_reverse=self.rule.gate_reverse)
        self._note_release(snapshot, gated=kind is not None)
        if kind is None:
            return decision, degrade, snapshot, False
        gated, reason, pending = evaluate_horizon_gate(
            decision, snapshot, self.rule, position_side=facts.side,
            priority_gates=(() if priority is None else priority.released_gates),
            may_pend=bool(priority is not None and priority.may_pend))
        if reason is None:
            self._note_pass(snapshot)
            return decision, degrade, snapshot, False
        if pending:
            self.last_pending_reason = reason
            return decision, degrade, snapshot, True
        self._note_veto(reason, kind)
        self.last_reason = reason
        return gated, (degrade or reason), snapshot, False

    # ---------- 离场优先级计数（exit-priority §5.4）----------
    def note_exit_priority(self, priority) -> None:
        """记一轮**最终**档位（须在反思之后调用，档位在那里才定型）；`None` / `none` ⇒ 空转。

        `override` 与 `adjudicated` **共用**同一份配额（X-4b）：两者都在此自增
        `exit_overrides_used`，下一轮的 `resolve_exit_priority` 读它做合取判定。
        """
        if priority is None or priority.level not in (LEVEL_EXPEDITE, LEVEL_OVERRIDE,
                                                      LEVEL_ADJUDICATED):
            self._note_final_declared(priority)
            return
        self.exit_levels[priority.level] += 1
        if priority.level in (LEVEL_OVERRIDE, LEVEL_ADJUDICATED):
            self.exit_overrides_used += 1
        self._note_final_declared(priority)

    def _note_final_declared(self, priority) -> None:
        """`final` 声明与其中被降档的笔数（X-9：这条通道必须能被事后证伪，否则无法被信任）。"""
        if priority is None or priority.declared != CONVICTION_FINAL:
            return
        self.exit_final_declared += 1
        if priority.level not in (LEVEL_OVERRIDE, LEVEL_ADJUDICATED):
            self.exit_final_downgraded += 1

    def note_exit_fail_open(self) -> None:
        """记一笔**未经复核官复核**的离场放行（【R:P1-3】：它不进 `degrade`，故必须有自己的计数）。"""
        self.exit_fail_open_rounds += 1

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

    # ---------- 开仓侧换手纪律（turnover-budget §2.2.5 / §2.3.4）----------
    def turnover_gate(self, decision, degrade: str | None, *, position_side: str,
                      now_wall: float) -> tuple[object, str | None]:
        """★ G4/G5 门禁（冷却之后、仓位/刹车之前）；未装配 ⇒ 原样直通、同一对象返回。

        判定读的是**本轮 `gate()` 刚刚 refresh 出来的那一份快照**——与判定同源，且由于 trace 存
        的是同一个 dict 引用，[P2-2] 的「原地 mutate 计数」在此生效：被拦轮的轨迹里
        `budgetVetoes` 已经 +1，不会落后一轮（S-17）。

        Args:
            decision: 本轮决策（已过铁律 / 周期 / 利润保护冷却三层）。
            degrade: 既有合成降级码（「先执行者胜出」，本层只在其为空时写入）。
            position_side: 当前**投影立场**（实盘 `executor.projected`，回测 `pos.side`）——
                [P0-1] 的三分支判定式依赖它，用真实持仓做判据会看不见同根新边沿。
            now_wall: 判定时刻（实盘墙钟秒，回测当前根 ts）。
        """
        self.last_turnover_reason = None
        if self.turnover_rule is None:
            return decision, degrade
        wire = (self.last_snapshot or {}).get("turnover")
        skip = gate_skip_reason(position_side)
        if skip is not None:                       # fail-open，但如实记号（绝不静默）
            if isinstance(wire, dict):
                wire["gateSkipped"] = skip
            return decision, degrade
        gated, reason = apply_turnover_gate(decision, wire, self.turnover_rule,
                                            position_side=position_side)
        if reason is None:
            return decision, degrade
        self.turnover_state = note_turnover_veto(self.turnover_state, reason)
        bump_wire_vetoes(wire, self.turnover_state)
        self.last_turnover_reason = reason
        return gated, (degrade or reason)

    def backstop_gate(self, decision, degrade: str | None, *,
                      position_side: str) -> tuple[object, str | None]:
        """★ G6 兜底覆盖闸（只拦同向加仓）；未核实 / 覆盖未知 ⇒ 恒放行（fail-open）。

        两个判定参数取自 `self.backstop.settings`——`BackstopRuntime` 本来就持有 settings，
        再让会话层把它们传一遍，等于在决策链上多铺一条可以与取数侧分叉的口径。本类自身
        仍然不持有 settings（回测侧 `backstop is None` ⇒ 整层空转）。
        """
        self.last_backstop_reason = None
        if self.backstop is None:
            return decision, degrade
        settings = self.backstop.settings
        gated, reason = apply_backstop_gate(
            decision, (self.last_snapshot or {}).get("backstop"),
            position_side=position_side,
            enabled=bool(getattr(settings, "ai_trade_backstop_gate_scale_in", True)),
            min_coverage=float(getattr(settings, "ai_trade_backstop_min_coverage", 0.999)))
        if reason is None:
            return decision, degrade
        self.backstop_vetoes += 1
        self.last_backstop_reason = reason
        return gated, (degrade or reason)

    def expire_turnover_cooldown(self, now_wall: float) -> bool:
        """G4 冷却由「激活」翻为「未激活」的那一 tick 返回 `True`（[P1-3] 补推 status 用）。

        仓库已为完全同款的问题写过专门补丁（`ProfitGuardRuntime.expire_cooldown`）：不推则前端
        chip 会一直挂着——后端已不再拦，UI 却说在拦。**预算不需要补推**：它是连续量，每次
        status 下发的 `tokens` / `nextTokenSeconds` 都是瞬时快照，前端只显示不自行推进。
        """
        rule = self.turnover_rule
        active = bool(rule is not None and rule.reentry_cooldown_s > 0
                      and self.turnover_state.cooldown_side in ("long", "short")
                      and float(now_wall) < float(self.turnover_state.cooldown_until_wall))
        expired = self._cooldown_was_active and not active
        self._cooldown_was_active = active
        return expired

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
        # 换手预算的消耗点（turnover-budget §2.2.4）：**只对新 epoch 计一次**——上面那条早退已经
        # 保证了「同 (side, seq) 不重复建 epoch」，故加仓（同 epoch 的后续成交）走不到这里。
        # 预算的单位是「往返」，与验收卡第 3 行的分子 `roundTrips` 严格同单位（D-4）。
        self.turnover_state = on_open_fill(self.turnover_state, self.turnover_rule,
                                           now_wall=float(now_wall))

    def note_close(self, trade: dict, now_wall: float) -> None:
        """平仓成交：记一笔真实持仓时长样本 + 一笔退出来源样本，并清空 epoch。

        平仓侧不存在部分结算（`_adopt_partial` 对 close 意图刻意不做增量采纳），故一个 epoch 恰对应
        一次 `note_close`。本方法是**唯一**同时握有「本次持仓时长」与「本次平仓成交」的地方，故
        退出来源归因也挂在这里；**记录必须发生在 `rule is None` 早退之前**（[P0-4]）。

        ⚠ **本轮唯一一处纯核心之外的调用顺序依赖（turnover-budget [P0-4]）**：G4 冷却的
        `closed_side` 必须在 `_consume_epoch_hold()` **之前**从 `self.state.epoch[0]` 取——该方法
        会把 `state` 整体重置。**绝不可**改从 `trade["side"]` 取：实盘 close 成交的它恒为字符串
        `"close"`、回测干脆传空字典 `{}`，那样 `cooldown_side` 会变成 `"close"`，而判定式比对的是
        `long` / `short` ⇒ **永远不拦、永远不报错**。`epoch is None`（从未建立 / 未装配）⇒
        不开启冷却（宁可不拦，也不拿一个猜的方向去拦）。
        """
        closed_side = self.state.epoch[0] if self.state.epoch else ""
        hold_s = self._consume_epoch_hold(now_wall)
        self.turnover_state = on_close_fill(self.turnover_state, self.turnover_rule,
                                            closed_side=closed_side,
                                            now_wall=float(now_wall))
        if self.record_exits:
            self.exits.record(trade, hold_seconds=hold_s)

    def _consume_epoch_hold(self, now_wall: float) -> float | None:
        """结算本 epoch 并返回真实持仓时长；未装配 / 无 epoch / 估算起点 ⇒ `None`。

        估算起点的样本**不入时长统计**（`opened_wall` 是兜底猜的，拿它算平均持仓时间等于用一个
        编出来的数去证明本层有效），但它照样计入 `round_trips` 与退出来源笔数。
        """
        if self.rule is None or self.state.epoch is None:
            return None
        self.round_trips += 1
        hold_s: float | None = None
        if self.state.opened_wall_estimated:
            self.estimated_epochs += 1
        else:
            hold_s = max(0.0, float(now_wall) - float(self.state.opened_wall))
            self.hold_samples.append(hold_s)
        self.state = HorizonState()
        return hold_s

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
                # 开仓侧换手纪律的三个只增计数（turnover-budget §4.3）。[P2-7] 刻意**不并进**
                # 上面四个第 1 轮的冻结计数：那会让 G-2 的 AST 冻结门禁虽过、语义却已漂移。
                "reentryVetoes": self.turnover_state.reentry_vetoes,
                "budgetVetoes": self.turnover_state.budget_vetoes,
                "opensCounted": self.turnover_state.opens_counted,
                # 离场优先级的七个只增计数（exit-priority §5.4）。同样**刻意不并进**上面的冻结
                # 计数：X-9 要的是「用掉了几次最高优先级、其中几次事后证明是对的」这个能查的数。
                "exitExpediteRounds": self.exit_levels[LEVEL_EXPEDITE],
                "exitOverrideRounds": self.exit_levels[LEVEL_OVERRIDE],
                "exitAdjudicatedRounds": self.exit_levels[LEVEL_ADJUDICATED],
                "exitFinalDeclared": self.exit_final_declared,
                "exitFinalDowngraded": self.exit_final_downgraded,
                "exitPendingReleases": self.release_reasons[RELEASE_EXIT_PENDING],
                "exitFailOpenRounds": self.exit_fail_open_rounds,
                **self._sample_stats()}

    # ---------- 周期验收卡的原始计数器（horizon-acceptance [P1-2]）----------
    def acceptance_input_parts(self) -> dict | None:
        """验收卡所需的**未 round 原始计数器**；未装配 ⇒ `None`。

        与 `stats()` 分工明确：后者是**展示口径**（`avgHoldSeconds` round 3 位、
        `withinTargetRate` round 4 位），**反推不出** `hold_seconds_sum` 与
        `within_target_samples`；按 `avg × n` 反乘会引入舍入误差，多成员合并后误差累积——那就与
        「原始计数器可精确合并」这句话自相矛盾了（[P1-2]）。`stats()` 一字不改。
        """
        if self.rule is None:
            return None
        samples = self.hold_samples
        return {"roundTrips": int(self.round_trips),
                "holdSecondsSum": float(sum(samples)),
                "holdSamples": len(samples),
                "withinTargetSamples": self._within_target_samples(samples),
                # 验收卡第 3 行的自指披露（turnover-budget §2.2.6）：本会话是否武装了 G5、
                # 它实际拦下多少次。未武装 ⇒ 该行 note 逐字节不变（冻结用例守）。
                "turnoverGateArmed": bool(self.turnover_rule is not None
                                          and self.turnover_rule.per_day is not None),
                "turnoverBudgetVetoes": int(self.turnover_state.budget_vetoes)}

    def _within_target_samples(self, samples: list[float]) -> int:
        """落在 `[targetMin, targetMax]` 内的**条数**（未设上界 ⇒ 0，由验收卡判 insufficient）。"""
        rule = self.rule
        if rule is None or rule.target_hold_max_s <= 0:
            return 0
        return sum(1 for s in samples
                   if rule.target_hold_min_s <= s <= rule.target_hold_max_s)

    def exit_stats(self) -> dict | None:
        """退出来源分布 wire；零样本 ⇒ `None`（**与 `rule` 是否装配无关**，[P0-4]）。"""
        return self.exits.stats()

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
