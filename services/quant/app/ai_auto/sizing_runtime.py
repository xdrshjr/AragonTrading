"""仓位 / 刹车层的**运行时载体**（ai-trade-vol-sizing-and-drawdown-brake §3.1 / §4.2）。

严格镜像仓库已经跑了两轮的同款范式（`ProfitGuardRuntime`、`CostRuntime`）：纯核心里只有公式与状态
机，本模块持会话级状态、装配 wire、记计数，会话侧只留薄封装调用（`session.py` 早已超 `CLAUDE.md` 的
800 行红线，只允许新增薄封装）。**本类不持有 `session` 引用**——需要 executor 断面时由调用方把
`executor` 当形参传进来（与 `profit_guard_runtime.build_position_facts` 的既有先例同款）。

模块内还承载 `executor.risk` 的**唯一写点**（§3.4 的落地缝）：本轮 ATR 止损距离在 `_do_open` 现算随单
OCO 触发价**之前**被写进 `executor.risk`，故 `live_auto/executor.py` 净增 0 行。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, replace

from .drawdown_brake import (
    BrakeCounters,
    BrakeRule,
    advance_equity,
    apply_brake_gate,
    brake_wire,
    evaluate_brake,
    initial_state,
    note_close as brake_note_close,
    parse_brake_rule,
    unrealized_share,
)
from .exposure_book import GroupExposureBook, exposure_wire
from .sizing import (
    SizingInputs,
    SizingOutcome,
    SizingRule,
    StopPlan,
    StopRule,
    annual_to_bar_vol,
    apply_size_fraction,
    parse_sizing_rule,
    parse_stop_rule,
    realized_vol_bar,
    resolve_size_fraction,
    resolve_stop_distance,
    sizing_wire,
    window_need,
)
from .stats_ext import build_sizing_stats, merge_sizing_stats

_GATED_ACTIONS = ("open_long", "open_short")
_ACTION_SIDE = {"open_long": "long", "open_short": "short"}
_EPS = 1e-12


# ============================ `executor.risk` 的唯一写点（[P0-1]）============================
def can_override_risk(executor, action: str | None = None) -> str | None:
    """能否安全改写 `executor.risk`；`None`=可以，否则返回拒绝原因（进 wire 的 `stopSource`）。

    这两条前置条件**不是防御性编程，是正确性条件**：

    - `resting is not None`：限价委托默认开启，`_do_open` 下单时按**当轮** risk 附了 OCO，但挂单可以
      跨若干轮才成交；`_adopt_fill` 在采纳成交时用**当时**的 `self.risk` 重算 tp/sl 并写进成交回执
      落库，而 `profit_guard_runtime.exchange_oco_px` 的**首选真源**恰恰是这条回执。期间改写 risk
      就会把「交易所侧根本不存在的止损价」写进落库回执（`ai-trade-limit-orders` P1-4 的原坑）。
    - `projected != "flat"`：持仓存续期改写 risk 会让 `exchange_oco_px` 的 `derived` 回落分支凭空造出
      一对并不存在的 OCO 价。

    刻意**不**建立在「同协程无 await」上：`_execute_decision` 第一件事就是 `async with self._exec_lock`，
    而 5s 盯市循环在同一把锁上——那是一个真实的让出点。

    Args:
        executor: `LiveExecutor` 断面。
        action: 本轮动作；持仓存续期的开仓即反手，单列 `skipped_reversal` 以便度量代价（L1.5）。
    """
    if getattr(executor, "resting", None) is not None:
        return "skipped_resting"
    projected = getattr(executor, "projected", "flat")
    if projected != "flat":
        if action in _GATED_ACTIONS and _ACTION_SIDE.get(action) != projected:
            return "skipped_reversal"
        return "skipped_position_live"
    return None


def apply_risk_override(executor, plan: StopPlan | None,
                        action: str | None = None) -> str:
    """把本轮 ATR 止损距离写进 `executor.risk`；返回实际生效的 `stopSource`（五态）。

    `plan is None` 或前置条件不满足 ⇒ **一行都不动**，逐字回退到当前 risk（构造期固定 pct，或上一次
    开仓时写入的那一份——两者都与交易所侧现存 OCO 一致）。`max_hold_bars` **必须原样保留**：
    `_maybe_time_exit` 读它，改动会静默改变时间止盈行为。

    **残留态的诚实声明**：写入后的 risk 会一直生效到下一次满足前置条件的开仓被覆盖为止。这是期望
    行为——OCO 已按该距离挂在交易所侧，会话内的展示口径必须跟着它走；而**正因为**它是残留态，改写
    窗口才必须收窄到「手上什么都没有」的那一刻。
    """
    if plan is None:
        return "fixed"
    blocked = can_override_risk(executor, action)
    if blocked is not None:
        return blocked
    executor.risk = replace(executor.risk, stop_loss_pct=plan.stop_pct,
                            take_profit_pct=plan.take_pct)
    return "atr"


def prior_p_win(prior_view: dict | None, action: str) -> float | None:
    """从 `PriorRuntime.coverage_wire()` 按方向取先验胜率；缺席 ⇒ `None` ⇒ 凯利项不参与 `min`。

    **绝不**用 0.5 之类的默认值凭空造先验（阶段二第一步 C-2 契约）：一个编出来的胜率会让凯利项看起来
    可算，而它描述的是一个不存在的统计。
    """
    side = _ACTION_SIDE.get(action)
    if not side or not prior_view:
        return None
    readout = prior_view.get(side) or {}
    value = readout.get("p") if isinstance(readout, dict) else None
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class SizingContext:
    """一次求解所需的会话 / 回测断面（聚合形参，满足 `CLAUDE.md` 形参 ≤5）。

    `notional_full` / `fallback_stop_pct` / `fallback_take_pct` 留空时由 `executor` 断面派生（实盘），
    回测侧无 executor 故必须显式给出。
    """

    ref_px: float | None = None
    atr_px: float | None = None
    p_win: float | None = None
    cost_bps: float | None = None
    step: float = 0.0
    now_wall: float = 0.0
    notional_full: float | None = None
    fallback_stop_pct: float | None = None
    fallback_take_pct: float | None = None
    closes: list | None = None          # 回测侧的收盘序列（实盘走 `note_candles` 的确认根喂数点）


@dataclass
class _Counters:
    """会话级累计观测（`aiStats.sizing` 的来源；轮级与事件级口径不可互相换算）。"""

    gated_rounds: int = 0
    scaled_rounds: int = 0
    scale_ratios: list[float] = field(default_factory=list)
    quantize_erased_rounds: int = 0
    atr_stop_rounds: int = 0
    reversal_skips: int = 0
    resting_skips: int = 0
    corr_scaled_rounds: int = 0
    exposure_unreachable_rounds: int = 0


class SizingRuntime:
    """一个会话的仓位 / 刹车运行时（纯同步；无 IO、无 await、不持 session 引用）。

    三条规则**各自独立装配**：`rule`（总闸①仓位求解）、`stop_rule`（总闸② ATR 止损，L1 只开这一个）、
    `brake_rule`（总闸④刹车三档）。三者全为 `None` ⇒ `armed` 为假 ⇒ 全部方法零动作、payload 无
    `sizingPlan` 键、trace 零事件、`executor.risk` 从不被赋值（S-4 的逐字节回退）。
    """

    def __init__(self, rule: SizingRule | None = None, stop_rule: StopRule | None = None,
                 brake_rule: BrakeRule | None = None, *, inst_id: str = "",
                 bar_seconds: int = 60, book: GroupExposureBook | None = None,
                 budget_usd: float = 0.0) -> None:
        self.rule = rule
        self.stop_rule = stop_rule
        self.brake_rule = brake_rule
        self.inst_id = inst_id
        self.bar_seconds = max(1, int(bar_seconds))
        self.book = book
        self.state = initial_state(budget_usd)
        self.brake_counters = BrakeCounters()
        self.counters = _Counters()
        self._vol: float | None = None           # 最近一次 `note_candles` 算出的每根已实现波动率
        self._vol_source = "insufficient_samples"
        self._last_wire: dict = {}               # 最近一次求解的读数（供 payload 段与前端回放）

    @property
    def armed(self) -> bool:
        """三条规则任一装配即为真；假 ⇒ 本层逐字节回退到合入前。"""
        return not (self.rule is None and self.stop_rule is None and self.brake_rule is None)

    @property
    def window_need(self) -> int:
        """本层需要的收盘价根数；未装配 ⇒ 0。式子的唯一真源在 `sizing.window_need`（复制即漂移）。"""
        return window_need(self.rule)

    # ---------- 门控（第 7 步：回撤刹车 / 连亏熔断）----------
    def gate(self, decision, degrade: str | None, *, now_wall: float | None = None) -> tuple:
        """第二 / 第三档把开仓硬降级为 hold；未装配或未触发 ⇒ 返回**同一个** decision 对象。

        排在反思之前（被拦轮此时已是 hold，反思对 hold 本就直通 ⇒ 自动省掉一次 LLM 调用）、排在共识与
        最小 edge 门禁之前（被拦轮不进 `edgeGate` 的计数分母，避免稀释 `vetoRate`）。

        `now_wall` 留空即取墙钟（实盘）；回测**必须**显式传本根 ts——冷却是由 `note_close` 用根 ts 开
        的，判定这一侧若改用墙钟，历史行情的到期时刻早已落在墙钟之后方，第三档在回测里恒不触发且不
        报错（免责文案 ④ 反而宣称它「按根 ts 计时」）。两侧时钟必须同源。
        """
        if self.brake_rule is None:
            return decision, degrade
        view = evaluate_brake(self.state, self.brake_rule,
                              now_wall=time.time() if now_wall is None else now_wall)
        decided, reason = apply_brake_gate(decision, view)
        if reason is not None:
            if view.level == "halt":
                self.brake_counters.halt_rounds += 1
                if view.unrealized_share is not None:
                    self.brake_counters.halt_unrealized_shares.append(view.unrealized_share)
            else:
                self.brake_counters.cooldown_hits += 1
        return decided, (degrade or reason)

    # ---------- 求解（第 11 步：仓位 + ATR 止损覆盖）----------
    def resolve_live(self, decision, executor, *, ref_px: float, atr_px, step: float,
                     prior_view: dict | None, cost_bps: float | None) -> tuple:
        """实盘侧一行入口：组装断面 → 求解 → 改写 `executor.risk`（会话只留一次调用）。"""
        return self.resolve(decision, SizingContext(
            ref_px=ref_px, atr_px=atr_px, step=step, now_wall=time.time(),
            p_win=prior_p_win(prior_view, decision.action), cost_bps=cost_bps), executor)

    def resolve(self, decision, ctx: SizingContext,
                executor=None) -> tuple[object, StopPlan | None, dict]:
        """解出本轮占比并落地 ATR 止损；未装配 ⇒ `(同一对象, None, {})`（调用方据此不发任何事件）。

        `executor` 非空（实盘）时本方法**自己**完成 `apply_risk_override`；回测侧不传，改由调用方拿
        返回的 `plan` 做 `_oco_risk` + `main.set_risk(...)` 的**双写**（[P0-3]：只改 `_oco_risk` 一行
        都不会生效）。

        Returns:
            `(decision, plan, wire)`——`wire` 为空 dict 即本层未装配。
        """
        if not self.armed:
            return decision, None, {}
        if ctx.closes is not None:      # 回测侧：无确认根喂数点，波动率随本轮断面一并刷新
            self._refresh_vol(ctx.closes)
        plan = resolve_stop_distance(atr_px=ctx.atr_px, ref_px=ctx.ref_px, rule=self.stop_rule)
        stop_source = self._apply_stop(executor, plan, decision.action)
        view = (evaluate_brake(self.state, self.brake_rule, now_wall=ctx.now_wall or time.time())
                if self.brake_rule is not None else None)
        outcome, exposure, corr = self._solve(decision, ctx, plan, view, executor)
        wire = sizing_wire(outcome, plan, stop_source, brake_wire(view),
                           exposure_wire(exposure, corr_scale=corr))
        wire["realizedVolAnnual"] = self._vol_annual()
        self._last_wire = wire
        return apply_size_fraction(decision, outcome), plan, wire

    def _apply_stop(self, executor, plan: StopPlan | None, action: str) -> str:
        """落地 ATR 止损覆盖并记三个 skip 计数（L1.5 用它度量「ATR 止损覆盖不到多少开仓」）。"""
        if executor is None:
            source = "atr" if plan is not None else "fixed"
        else:
            source = apply_risk_override(executor, plan, action)
        if source == "atr":
            self.counters.atr_stop_rounds += 1
        elif source == "skipped_resting":
            self.counters.resting_skips += 1
        elif source == "skipped_reversal":
            self.counters.reversal_skips += 1
        return source

    def _solve(self, decision, ctx: SizingContext, plan: StopPlan | None,
               view, executor) -> tuple[SizingOutcome, object, float]:
        """两阶段求解：先以 `corr_scale=1` 得到提议名义额，再据它解相关性缩减，最后重解一次。

        两阶段是必需的：`corr_scale` 依赖本成员**提议的**名义额，而提议名义额又依赖 `f`——直接一次求解
        会形成循环。纯函数重跑一次的代价是几十条浮点运算，换来的是一个可证明的不变式（S-15）。
        """
        if self.rule is None or decision.action not in _GATED_ACTIONS:
            return SizingOutcome(fraction=None), None, 1.0
        stop_bps, take_bps = self._bracket_bps(plan, ctx, executor)
        base = SizingInputs(
            action=decision.action, model_fraction=decision.size_fraction, p_win=ctx.p_win,
            cost_bps=ctx.cost_bps, stop_bps=stop_bps, take_bps=take_bps,
            realized_vol_bar=self._vol, brake_mult=(1.0 if view is None else view.mult),
            corr_scale=1.0, step=ctx.step)
        pre = resolve_size_fraction(base, self.rule, self.bar_seconds)
        exposure, corr = self._correlation(decision.action, pre, ctx, executor)
        outcome = (pre if corr >= 1.0 - _EPS
                   else resolve_size_fraction(replace(base, corr_scale=corr), self.rule,
                                              self.bar_seconds))
        self._tally(outcome, base, exposure, corr)
        return outcome, exposure, corr

    def _bracket_bps(self, plan: StopPlan | None, ctx: SizingContext,
                     executor) -> tuple[float | None, float | None]:
        """凯利赔率的 `W`/`L`：本轮要挂的括号；ATR 止损未装配 ⇒ 退回当前固定 pct（[P0-2] 同源律）。"""
        if plan is not None:
            return plan.stop_bps, plan.take_bps
        stop_pct, take_pct = ctx.fallback_stop_pct, ctx.fallback_take_pct
        if executor is not None:
            risk = getattr(executor, "risk", None)
            stop_pct = getattr(risk, "stop_loss_pct", None)
            take_pct = getattr(risk, "take_profit_pct", None)
        return (None if stop_pct is None else float(stop_pct) * 1e4,
                None if take_pct is None else float(take_pct) * 1e4)

    def _correlation(self, action: str, pre: SizingOutcome, ctx: SizingContext,
                     executor) -> tuple[object, float]:
        """组合有效敞口与缩减系数；未配置上限 / 无登记簿 ⇒ `(None, 1.0)`（本层不生效）。"""
        if self.book is None or self.rule.exposure_cap_mult <= 0 or pre.fraction_raw is None:
            return None, 1.0
        full = ctx.notional_full
        if full is None and executor is not None:
            full = float(getattr(executor, "budget_usd", 0.0)) * float(
                getattr(executor, "leverage", 1.0))
        if not full:
            return None, 1.0
        sign = 1.0 if action == "open_long" else -1.0
        view = self.book.effective_exposure(
            inst_id=self.inst_id, proposed_notional=sign * pre.fraction_raw * float(full),
            min_samples=self.rule.corr_min_samples)
        return (None, 1.0) if view is None else (view, view.scale)

    def _tally(self, outcome: SizingOutcome, base: SizingInputs, exposure,
               corr: float) -> None:
        """轮级计数：本层是否真的缩了、缩了多少、以及是否被档位地板整个吃掉（[P1-2]）。

        `quantizeErasedRounds` 与 `brakeSlowRounds` 必须分开看：后者只说明「第一档在这一轮是激活的」，
        前者才说明「那次减速被档位地板原样吃回去了」——只凭 `brakeSlowRounds` 宣称刹车已验证，正是本
        仓库最忌讳的那种假阳性观测。
        """
        self.counters.gated_rounds += 1
        entry = base.model_fraction if base.model_fraction is not None else outcome.fraction_raw
        if entry and outcome.fraction_raw is not None and outcome.fraction_raw < entry - _EPS:
            self.counters.scaled_rounds += 1
            self.counters.scale_ratios.append(outcome.fraction_raw / entry)
        if outcome.scale_erased:
            self.counters.quantize_erased_rounds += 1
        if base.brake_mult < 1.0 - _EPS:
            self.brake_counters.slow_rounds += 1
        if corr < 1.0 - _EPS:
            self.counters.corr_scaled_rounds += 1
        if exposure is not None and exposure.unreachable:
            self.counters.exposure_unreachable_rounds += 1

    # ---------- 喂数 ----------
    def note_equity(self, equity: float, *, unrealized_usd=None,
                    session_pnl_usd=None) -> None:
        """推进刹车的权益 / 峰值 / 最大回撤（回测侧只有权益一个数）。"""
        if self.brake_rule is None:
            return
        share = (None if unrealized_usd is None
                 else unrealized_share(unrealized_usd, session_pnl_usd))
        self.state = advance_equity(self.state, equity, self.brake_rule, unrealized_share=share)

    def note_executor(self, executor, mark_px: float | None = None) -> None:
        """实盘侧一行喂数：权益推进 + 敞口簿上报（[P1-8] 的轮末兜底同挂这里）。"""
        if not self.armed:
            return
        self.note_equity(float(executor.budget_usd) + float(executor.session_pnl_usd),
                         unrealized_usd=executor.unrealized_usd,
                         session_pnl_usd=executor.session_pnl_usd)
        self.note_notional(executor, mark_px)

    def note_notional(self, executor, mark_px: float | None = None) -> None:
        """把当前 executor 断面折成**带符号**名义额上报敞口簿（`flat` ⇒ 0.0）。

        [P1-8] 挂在 `session._dispatch`（全部 executor 事件的唯一收口）而**不是**决策轮之后：限价挂单
        期间 `projected` / `entry_qty_sz` 刻意不写（幻影成交防线），成交由 `_poll_resting → _adopt_fill`
        在决策轮之外采纳——只挂决策轮的话，一笔已经真实成交的持仓在该成员下一轮决策之前对敞口簿的
        贡献恒为 0，而其余成员正是在这段时间里读它。
        """
        if self.book is None:
            return
        side = getattr(executor, "projected", "flat")
        px = mark_px if mark_px else getattr(executor, "entry_price", 0.0)
        sign = 1.0 if side == "long" else -1.0 if side == "short" else 0.0
        notional = sign * float(executor.entry_qty_sz) * float(executor.ct_val) * float(px or 0.0)
        self.book.publish_notional(self.inst_id, notional)

    def note_close(self, trade, *, now_wall: float) -> None:
        """一笔平仓成交推进连亏计数（`trade` 可为成交 dict 或裸 pnl 数值）。"""
        if self.brake_rule is None:
            return
        pnl = trade.get("pnl") if isinstance(trade, dict) else trade
        self.state = brake_note_close(self.state, pnl, self.brake_rule, now_wall=now_wall)

    def _refresh_vol(self, closes) -> None:
        """重算每根已实现波动率（未装配仓位规则 ⇒ 零动作）。"""
        if self.rule is not None:
            self._vol, self._vol_source = realized_vol_bar(closes, self.rule.vol_window)

    def note_candles(self, closes, as_of_ts: int) -> None:
        """确认根落地：重算已实现波动率 + 把对数收益发布到分组敞口簿（零额外 IO）。"""
        if self.rule is None:
            return
        self._refresh_vol(closes)
        if self.book is not None:
            need = self.window_need
            rows = list(closes)
            self.book.publish_returns(self.inst_id, rows[-need:] if need else rows, as_of_ts)

    # ---------- 只读 ----------
    def inject(self, payload: dict) -> None:
        """把本层读数注入本轮 payload；未装配 ⇒ **整键缺席** ⇒ prompt 逐字节回退（零回归范式）。"""
        plan = self.plan_wire()
        if plan:
            payload["sizingPlan"] = plan

    def plan_wire(self) -> dict | None:
        """`payload["sizingPlan"]`：刹车与波动率读数**取当下**，凯利与括号读数取**最近一次求解**。

        求解发生在轮次末尾（第 11 步）而 payload 在轮首装配，故本层的凯利 / 括号读数天然是「最近一次
        开仓意愿轮解出来的那一份」——这不是 bug，是时序：轮首根本还没有 decision，也就没有成本查询与
        括号可言。刹车档位与回撤则是纯状态量，必须取当下（模型要据它决定这一轮敢不敢开）。
        """
        if not self.armed:
            return None
        wire = dict(self._last_wire)
        if self.brake_rule is not None:
            wire.update(brake_wire(evaluate_brake(self.state, self.brake_rule,
                                                  now_wall=time.time())))
        wire["realizedVolBar"] = self._vol
        wire["volSource"] = ("disabled" if self.rule is None or self.rule.vol_target_annual <= 0
                             else self._vol_source)
        wire["realizedVolAnnual"] = self._vol_annual()
        return wire or None

    def merge_into(self, ai_stats: dict) -> dict:
        """把 `sizing` 加法键并入既有 `aiStats`（键冲突即抛，绝不静默覆盖）。"""
        return merge_sizing_stats(ai_stats, self.snapshot())

    def snapshot(self) -> dict:
        """`aiStats.sizing` 的唯一装配点（会话内读数与落库富化同出一次调用）。"""
        return build_sizing_stats(self.counters, self.brake_counters, self.state)

    def _vol_annual(self) -> float | None:
        """把每根波动率还原成年化（仅展示；判定一律走单根口径，绝不与年化直接相除）。"""
        if not self._vol:
            return None
        unit = annual_to_bar_vol(1.0, self.bar_seconds)
        return None if not unit else round(self._vol / unit, 6)


def build_sizing_runtime(settings, inst_id: str, bar_seconds: int, *,
                         book: GroupExposureBook | None = None,
                         budget_usd: float = 0.0) -> SizingRuntime:
    """从 `settings` 解析三条规则并装配运行时（[P1-7]：**不经启动请求**，两侧各自解析）。

    `AiTradeGroupStartRequest` 一个字段都不加：`base_rule` / `profit_rule` 是在 **API 层**解析后塞进
    请求的，而那里正是该请求的唯一构造方；给它加字段将永远没人填、规则恒 `None`、整个特性是死的且
    不报错。实盘走 `AiTradeManager._build_member`、回测走 `AiBacktestRunner.__init__`，两侧都已持有
    `settings`。
    """
    return SizingRuntime(
        parse_sizing_rule(settings), parse_stop_rule(settings),
        parse_brake_rule(settings, bar_seconds),
        inst_id=inst_id, bar_seconds=bar_seconds, book=book, budget_usd=budget_usd)


def sizing_config_wire(settings, bar_seconds: int) -> dict | None:
    """三条规则 → camelCase 回执镜像（run `config_json` 落库）；全未装配 ⇒ `None`。

    不回填这份配置，一条 run 的 `config_json` 就无法复现这次会话到底开了哪几个总闸——而四个总闸是
    本增量唯一的回滚手段，事后查不出「当时开着什么」等于回滚判断无从做起。
    """
    rule, stop = parse_sizing_rule(settings), parse_stop_rule(settings)
    brake = parse_brake_rule(settings, bar_seconds)
    if rule is None and stop is None and brake is None:
        return None
    wire: dict = {}
    if rule is not None:
        wire["sizing"] = {"volTargetAnnual": rule.vol_target_annual,
                          "volWindow": rule.vol_window, "kellyLambda": rule.kelly_lambda,
                          "exposureCapMult": rule.exposure_cap_mult,
                          "corrMinSamples": rule.corr_min_samples}
    if stop is not None:
        wire["stop"] = {"stopAtrMult": stop.stop_atr_mult, "stopRr": stop.stop_rr}
    if brake is not None:
        wire["brake"] = {"slowDdPct": brake.slow_dd_pct, "slowSizeMult": brake.slow_size_mult,
                         "haltDdPct": brake.halt_dd_pct, "lossStreak": brake.loss_streak,
                         "lossCooldownSeconds": brake.loss_cooldown_s}
    return wire
