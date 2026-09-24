"""利润保护层的**运行时状态载体**（ai-trade-profit-protection-adaptive §2.0 / F0）。

第 1 轮把保护层的状态机、快照装配、计数与 payload 注入全部原地写在 `AiTradeSession` 上，使该文件涨到
1397 行（CLAUDE.md 800 行红线的 1.7 倍）。本模块按「有没有 `await` / 碰不碰 executor 的下单路径」把
**纯同步的那一半**搬出来：

- 迁出：状态字段 + `refresh()`（推进 epoch → 组装快照）/ `update_hold_streak()` / `inject()` / 计数。
- 留在会话：`_maybe_profit_exit`（async 编排）/ `_apply_profit_exit`（`_exec_lock` + executor）/
  `_report_profit_exit`（事件判据）/ `_exchange_oco_px`（读 `executor.trades`）/ `_position_facts`。

**本类不持有 `session` 引用**（否则等于没抽）：`refresh()` 收一个由会话侧组装好的 `PositionFacts`，
自己只做「推进状态 → 组装快照 → 计数 → 注入 payload」。F0 步骤本身是**纯搬运、零行为变化**（G-0）；
本轮新增的 ATR 与冷却状态只是多两个**载体**字段，判定逻辑仍全部落在 `profit_guard` 纯核心里。
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .profit_cooldown import CooldownState, build_cooldown_view
from .profit_guard import (
    ARMED_STATUSES,
    PositionFacts,
    ProfitGuardRule,
    ProfitGuardState,
    advance_state,
    build_idle_snapshot,
    build_snapshot,
    clamp_commitment,
    is_commit_armed,
    ratchet_commitment,
    unavailable_reason_for,
)


@dataclass(frozen=True)
class SessionReadings:
    """会话侧的六个时钟 / 断面读数（聚合结构体，满足 CLAUDE.md「形参 ≤ 5」）。

    Attributes:
        seq: 会话级单调持仓序号（`_dispatch` 观察到 close 成交即 +1），epoch 身份的一半。
        now_wall: 当前墙钟。
        mark_px: **判定价**，恒取 `AiTradeSession._current_px()`（§2.2 单源硬约束）。
        bar_index: 最近确认根索引（算 `barsInPosition`，**仅展示**，绝不参与判定）。
        px_age_s: 判定价年龄（秒）。
        atr_px: 确认根 ATR；`None` ⇒ 回吐距离的上下界整体不生效。
    """

    seq: int
    now_wall: float
    mark_px: float
    bar_index: int
    px_age_s: float
    atr_px: float | None = None


def build_position_facts(executor, readings: SessionReadings, *,
                         cost_floor_bps: float | None = None) -> PositionFacts:
    """把 executor 的持仓读数 + 会话时钟组装成一次评估所需的断面（F0：由 session 迁出）。

    判定价恒取 `readings.mark_px`（`_current_px()`），**绝不**取最近确认根收盘价——15m 周期上后者
    最坏滞后一整根，正是诊断根因 ④ 要治的东西。
    """
    tp_px, sl_px, oco_source = exchange_oco_px(executor, executor.projected)
    return PositionFacts(
        seq=readings.seq, side=executor.projected, entry_px=executor.entry_price,
        leverage=executor.leverage, qty_coin=executor.entry_qty_sz * executor.ct_val,
        mark_px=readings.mark_px, now_wall=readings.now_wall,
        # barsInPosition **仅供展示**，绝不参与判定（时钟单一性，§2.2）。
        bars_in_position=((readings.bar_index - executor.entry_bar)
                          if executor.entry_bar >= 0 else None),
        exchange_tp_px=tp_px, exchange_sl_px=sl_px, exchange_oco_source=oco_source,
        px_age_s=readings.px_age_s, atr_px=readings.atr_px, cost_floor_bps=cost_floor_bps)


def exchange_oco_px(executor, side: str) -> tuple[float | None, float | None, str]:
    """交易所侧兜底 OCO 触发价（P1-4）：**首选最近一笔开仓成交回执**，取不到才只读转发现算。

    限价成交路径下 `entry_price` 被 `_adopt_fill` 置为真实成交价 `avgPx`，而随单 OCO 是按**挂单价**
    附的——照 `entry_price` 现算会得到一个交易所侧根本不存在的价。`_make_trade` 已把下单时的真实
    触发价如实落进 trade，那才是事实真源。
    """
    for trade in reversed(executor.trades):
        if trade.get("side") not in ("long", "short"):
            continue
        tp_px, sl_px = trade.get("takeProfitPrice"), trade.get("stopLossPrice")
        if tp_px is not None or sl_px is not None:
            return tp_px, sl_px, "trade"
        break        # 最近一笔开仓确实没挂 OCO：现算也只会得到同样的空，不再回溯更早的持仓
    tp_px, sl_px = executor.risk_trigger_px(side, executor.entry_price)
    return tp_px, sl_px, "derived"


class ProfitGuardRuntime:
    """一个会话的利润保护层运行时（纯同步；无 IO、无 await、不碰 executor）。

    Attributes:
        rule: 已装配规则；`None` = 本会话不装配（全部方法退化为零动作）。
        state: 当前持仓周期状态（无仓 / 已平仓 → None）。
        cooldown: 保护性平仓后的冷却态（F2；未激活 → None）。
        hold_streak: 连续「浮盈状态下继续持有」轮数（§2.6 c）。
        exits: **实际产生了平仓成交**的保护触发次数（事件级，P1-1）。
        armed_rounds: 快照曾处于 armed/breakeven_armed 的轮数（轮级，P2-2）。
        last_peak_pnl_usd: 最近一次**可用**快照的峰值浮盈（F4；读取即消费）。
        warned_seq: 平仓未成交 warn 的每持仓周期节流游标（P2-8）。
        pending_protect_note: 下一轮 payload 的一次性平仓回执（读取即消费，P1-7）。
        committed_exit_px: 已棘轮的承诺退出线（本轮 C2；None=本 epoch 尚无承诺）。
        committed_exit_basis: 当前承诺线被哪条约束定住（展示用）。
        committed_exit_epoch: 承诺线所属 epoch 的 `(side, seq)` 身份戳（[P0-1]）——承诺线**是** epoch
            作用域状态，必须与 `peak_px` 共用 `(side, seq)` 身份，随 `advance_state` 的新 epoch 复位。
    """

    def __init__(self, rule: ProfitGuardRule | None, *, peak_tracking_always: bool = False):
        self.rule = rule
        # ★ E-3（loss-and-liveness-hardening 轴 E）：峰值浮盈与保护层规则**解耦**。
        # 合入前 `peak_pnl` 在 257 笔平仓里 213 笔为 NULL（82.9%），根因就是「规则未装配 ⇒
        # `refresh` 第一行 return ⇒ 峰值永远不被缓存」——恰恰关掉了唯一能证明该开保护层的证据。
        # 峰值浮盈是纯派生量（`qty × signed(peak_px − entry)`），不需要任何规则就能算。
        # 默认 `False` 使既有直接构造该运行时的单测逐字节零回归；生产由会话按总闸传入。
        self.peak_tracking_always = bool(peak_tracking_always)
        self.state: ProfitGuardState | None = None
        self.cooldown: CooldownState | None = None
        self.hold_streak = 0
        self.exits = 0
        self.armed_rounds = 0
        self.last_peak_pnl_usd: float | None = None
        self.warned_seq: int | None = None
        self.pending_protect_note: dict | None = None
        self.committed_exit_px: float | None = None
        self.committed_exit_basis: str | None = None
        self.committed_exit_epoch: tuple | None = None

    # ---------- 状态推进 / 快照 ----------
    def clear_state(self) -> None:
        """持仓已消失（flat / 保护平仓成交）：丢弃 epoch 状态与承诺线，下一仓从 entry 重新起算。

        [P0-1] 承诺线**三清**：epoch 结束（flat / 保护平仓）时承诺线随 `peak_px` 一并作废。但仅靠此处
        清空**不够**——原子反手 `long→short` 不经 flat、`clear_state()` 不触发，故 `refresh` 还须按
        `committed_exit_epoch` 与新 epoch 比对再作废一次（见 `refresh`）。
        """
        self.state = None
        self.committed_exit_px = None
        self.committed_exit_basis = None
        self.committed_exit_epoch = None

    def refresh(self, facts: PositionFacts, *, stale_feed: bool) -> dict | None:
        """推进 epoch / 峰值价并组装 wire 快照（**纯同步、无 await**）；未装配 → None。

        epoch 身份判定与峰值单调推进全部委托 `profit_guard.advance_state`（§2.3.1 的 `(side, seq)`）；
        fail-open 前置校验委托 `unavailable_reason_for`，其结论如实进快照而非被静默吞掉。

        Args:
            facts: 会话侧组装的持仓 / 行情断面（判定价恒为 `_current_px()`）。
            stale_feed: 会话级行情降级标志（`_stale_degraded`）。

        Returns:
            camelCase wire 快照；规则未装配 → None。
        """
        if self.rule is None:
            return self._peak_only(facts, stale_feed=stale_feed)
        reason = unavailable_reason_for(facts, self.rule, px_age_s=facts.px_age_s,
                                        stale_feed=stale_feed)
        self.state = advance_state(self.state, facts)
        # [P0-1] 承诺线绑定 `(side, seq)` epoch 身份：`advance_state` 一旦因反手（side 变）/ 同向重开
        # （seq 变）/ 首仓（state 曾 None）产出新 epoch，旧承诺线**必须先作废**——否则旧多头承诺线会
        # 被带进新空头 epoch 并当场强平新仓（executor 的原子反手不经 flat、clear_state 不触发）。
        epoch = (self.state.side, self.state.seq)
        if self.committed_exit_epoch != epoch:
            self.committed_exit_px = None
            self.committed_exit_basis = None
            self.committed_exit_epoch = epoch
        # 把当前棘轮值注入本轮断面（承诺线经此进 build_snapshot 的第三条下方线与归因）。
        facts = replace(facts, committed_exit_px=self.committed_exit_px,
                        committed_exit_basis=self.committed_exit_basis)
        snapshot = build_snapshot(self.rule, self.state, facts, unavailable_reason=reason)
        if reason is None:
            # F4：只缓存**可用**快照的峰值浮盈——unavailable 快照的派生量一律 None，缓存它等于用
            # 一个不作数的数字去当捕获率分母。
            self.last_peak_pnl_usd = snapshot.get("peakUnrealizedPnlUsd")
        return snapshot

    def _peak_only(self, facts: PositionFacts, *, stale_feed: bool) -> None:
        """★ E-3：保护层未装配时的**只算峰值、不做任何判定**分支；**恒返回 `None`**。

        返回 `None` 是硬约束：调用点据它决定 payload 带不带 `profitGuard` 键，返回一个快照就等于
        在未装配的会话里凭空多出一段 prompt。本分支只做两件事——推进**同一个** `advance_state`
        （绝不另写第二份 epoch 判据，两份必漂移）、把峰值浮盈缓存进 `last_peak_pnl_usd`。

        fail-open 前置校验复用 `unavailable_reason_for(facts, None, …)`：判定价不可信 / 仓位读数
        不成立 / 行情降级时**不缓存**——那正是 F4 立下的纪律（用一个不作数的数字当捕获率分母，
        比没有这个数更糟）。缓存不上时 `last_peak_pnl_usd` 保持 `None`，**绝不填 0**。
        """
        if not self.peak_tracking_always:
            return None
        reason = unavailable_reason_for(facts, None, px_age_s=facts.px_age_s,
                                        stale_feed=stale_feed)
        self.state = advance_state(self.state, facts)
        if reason is None:
            # 与 `profit_guard._derive` 的 `peakUnrealizedPnlUsd` 逐字同式：
            # `qty_coin × signed(side, peak_px − entry)`。
            delta = self.state.peak_px - facts.entry_px
            self.last_peak_pnl_usd = facts.qty_coin * (
                delta if facts.side == "long" else -delta)
        return None

    def record_commitment(self, proposed_px, snapshot: dict | None) -> None:
        """把模型本轮承诺的退出线经**武装门控 → 纯几何夹紧 → 单调棘轮**记进运行时（本轮 C2）。

        读的是**本轮快照**（模型据以决策的那一份），故承诺线与模型看到的现价 / 保本线 / ATR 同源、
        可解释。未装配 / 承诺关 / 无快照 / 未武装一律短路（承诺线不会覆盖前值——棘轮的执行力来自此）。

        [P1-1] 武装门控用**唯一谓词** `is_commit_armed`（与回测同一份）；[P1-1] `clamp_commitment` 纯
        几何、不传 armed；[P1-2] 棘轮只此一处 `ratchet_commitment`（实盘与回测同 import 同调）。
        """
        if self.rule is None or not self.rule.commit_enabled or not snapshot:
            return
        if not is_commit_armed(snapshot.get("status")):
            return
        line, basis = clamp_commitment(
            proposed_px, side=snapshot.get("side"), mark_px=snapshot.get("livePx"),
            entry_px=snapshot.get("entryPx"), breakeven_px=snapshot.get("breakevenPx"),
            atr_px=snapshot.get("atrPx"), atr_floor_mult=self.rule.atr_floor_mult)
        self.committed_exit_px, self.committed_exit_basis = ratchet_commitment(
            self.committed_exit_px, self.committed_exit_basis, line, basis,
            side=snapshot.get("side"))

    # ---------- 计数 / 注入 ----------
    def update_hold_streak(self, stage1_action: str, stage1_degrade: str | None,
                           snapshot: dict | None) -> None:
        """连续「浮盈状态下继续持有」计数（§2.6 c 的逐分支枚举，不留二义）。

        与 `_update_hold_streak` 同以**第一阶段**决策为准（共识门禁 / 反思之前）：非 hold ⇒ 归零；
        hold 且降级 ⇒ 不变（既非真实观望也非交易意愿）；hold 无降级则按当前浮盈正负 +1 / 归零。
        """
        if stage1_action != "hold":
            self.hold_streak = 0
            return
        if stage1_degrade is not None:
            return
        return_pct = (snapshot or {}).get("returnPct")
        if return_pct is not None and return_pct > 0:
            self.hold_streak += 1
        else:
            self.hold_streak = 0

    def inject(self, payload: dict, snapshot: dict | None, *,
               note_after_rounds: int, now_wall: float) -> None:
        """把保护快照 / 一次性回执 / 冷却视图注入本轮 payload（未装配 ⇒ **整键缺席**）。

        [P1-7] `lastExit` **读取即消费**：它是一次性事件回执，若只写不清会每轮都注入同一条早已过去的
        强平，并经 `commit_round` 反复写进 `_context`/`_journal`——既污染上下文窗口，又会让模型误以为
        自己反复被强平。这与每轮重算的 `holdStreak` 注入范式不同，勿照抄。

        Args:
            payload: 本轮决策 payload（原地写）。
            snapshot: `refresh()` 产物；无持仓时为 None。
            note_after_rounds: 盈利持仓自察注记阈值（0=关闭）。
            now_wall: 冷却剩余秒数的计时基准（实盘墙钟）。
        """
        if self.rule is None:
            return
        note, self.pending_protect_note = self.pending_protect_note, None
        cooldown_view = build_cooldown_view(self.cooldown, now_wall=now_wall)
        if cooldown_view is not None:
            payload["profitGuardCooldown"] = cooldown_view
        if snapshot is None and note is None:
            return
        # 平仓后已 flat 但仍欠一条回执时，用空壳快照承载它——否则模型永远不知道仓位为何消失。
        wire = dict(snapshot) if snapshot is not None else build_idle_snapshot(self.rule)
        wire["lastExit"] = note
        if wire.get("status") in ARMED_STATUSES:
            self.armed_rounds += 1      # 轮级口径（P2-2），与事件级 exits 不可换算
        payload["profitGuard"] = wire
        if note_after_rounds > 0 and self.hold_streak >= note_after_rounds:
            payload["profitHoldRounds"] = self.hold_streak

    # ---------- F4：峰值浮盈回执（读取即消费）----------
    def take_peak_pnl(self) -> float | None:
        """取出并清空最近一次可用快照的峰值浮盈（`lastExit` 同范式，P1-7）。

        清空是必需的：不清则下一笔平仓会继承上一个持仓周期的峰值，把捕获率算到一笔与它无关的往返上。
        无快照 / 一路 unavailable ⇒ `None`，**绝不填 0**（0 会被聚合当成「峰值就是 0」）。
        """
        value, self.last_peak_pnl_usd = self.last_peak_pnl_usd, None
        return value

    # ---------- F2：冷却 ----------
    def start_cooldown(self, *, side: str, reason_code: str, label: str,
                       now_wall: float) -> None:
        """保护层**确实平掉了一笔**之后开启冷却（唯一写点，§2.2.1）；`cooldown_s<=0` ⇒ 不开启。"""
        if self.rule is None or self.rule.cooldown_s <= 0:
            return
        self.cooldown = CooldownState(
            until_wall=now_wall + self.rule.cooldown_s, side=side,
            reason_code=reason_code, label=label, scope=self.rule.cooldown_scope)

    def expire_cooldown(self, now_wall: float) -> bool:
        """冷却到期即清空；返回「本次调用是否发生了由激活→未激活的跃迁」（供主动补推 status）。"""
        if self.cooldown is None:
            return False
        if now_wall < self.cooldown.until_wall:
            return False
        self.cooldown = None
        return True
