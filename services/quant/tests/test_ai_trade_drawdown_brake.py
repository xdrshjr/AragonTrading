"""三级回撤刹车与连亏熔断纯核心单测（vol-sizing-and-drawdown-brake §7.1 T-9/T-13/T-14/T-15/T-30）。

四条必须可证伪的判据：
- **只拦开仓（S-9）**：四动作 × 三档穷举，`close` / `cancel_order` / `hold` 被拦次数恒为 0；
- **滞回**：跨过第二档后在滞回带内震荡不解除，跌回第一档阈值以下才解除（纯锁存会造出僵尸会话）；
- **连亏三分支**：`pnl>0` 归零 / `<0` 累加 / `==0` 不动（平局既不算连亏也不清算已有连亏）；
- **按杠杆定标**：同一标的价反向幅度在 1× 与 3× 下产出的档位按 §3.6 折算表一致。
"""
from __future__ import annotations

from app.ai_auto.decider import TradeDecision
from app.ai_auto.drawdown_brake import (
    BrakeRule,
    advance_equity,
    apply_brake_gate,
    brake_wire,
    evaluate_brake,
    initial_state,
    note_close,
    parse_brake_rule,
    unrealized_share,
)
from app.ai_auto.profit_cooldown import CooldownState, apply_profit_cooldown
from app.config import Settings

_BUDGET = 1000.0


def _rule(**over) -> BrakeRule:
    base = {"slow_dd_pct": 0.08, "slow_size_mult": 0.5, "halt_dd_pct": 0.15,
            "loss_streak": 4, "loss_cooldown_s": 3600.0}
    base.update(over)
    return BrakeRule(**base)


def _at(rule: BrakeRule, equity: float, state=None, **over):
    state = state if state is not None else initial_state(_BUDGET)
    return advance_equity(state, equity, rule, **over)


# ---------- 关闭值 ⇒ 整层不装配 ----------
def test_parse_brake_rule_off_by_default():
    assert parse_brake_rule(Settings(), 900) is None


def test_parse_brake_rule_scales_cooldown_by_bar():
    rule = parse_brake_rule(Settings(ai_trade_brake_loss_streak=4,
                                     ai_trade_brake_loss_cooldown_bars=4.0), 900)
    assert rule is not None and rule.loss_cooldown_s == 3600.0


# ---------- T-9：平仓与撤单永不被拦（S-9 穷举）----------
def test_close_and_cancel_never_blocked():
    rule = _rule()
    states = {"none": _at(rule, 1000.0),
              "slow": _at(rule, 900.0),
              "halt": _at(rule, 800.0),
              "cooldown": note_close(initial_state(_BUDGET), -1.0,
                                     _rule(loss_streak=1), now_wall=0.0)}
    blocked = 0
    for state in states.values():
        view = evaluate_brake(state, rule, now_wall=0.0)
        for action in ("open_long", "open_short", "close", "hold", "cancel_order"):
            decision = TradeDecision(action=action, size_fraction=0.5, confidence=0.5, reason="r")
            out, reason = apply_brake_gate(decision, view)
            if action in ("close", "cancel_order", "hold"):
                assert out is decision and reason is None
            elif reason is not None:
                blocked += 1
                assert out.action == "hold"
    assert blocked > 0                       # 反向保证本用例不是因为「什么都没触发」而恒真


# ---------- 三档语义 ----------
def test_slow_tier_only_scales_and_never_vetoes():
    view = evaluate_brake(_at(_rule(), 910.0), _rule(), now_wall=0.0)   # dd=9% ≥ 8%
    assert view.level == "slow" and view.mult == 0.5 and view.reason is None


def test_halt_tier_downgrades_open_to_hold_with_parametrised_code():
    view = evaluate_brake(_at(_rule(), 800.0), _rule(), now_wall=0.0)   # dd=20% ≥ 15%
    assert view.level == "halt"
    decision = TradeDecision(action="open_long", size_fraction=0.5, confidence=0.6, reason="r")
    out, reason = apply_brake_gate(decision, view)
    assert out.action == "hold"
    assert reason is not None and reason.startswith("drawdown_brake_halt(dd=")


# ---------- T-13：滞回，绝不锁存到会话结束 ----------
def test_brake_halt_uses_hysteresis():
    rule = _rule()
    state = _at(rule, 800.0)                                   # dd=20% ⇒ 锁存 halt
    assert evaluate_brake(state, rule, now_wall=0.0).level == "halt"
    state = advance_equity(state, 890.0, rule)                 # dd=11%：在滞回带内 ⇒ 不解除
    assert evaluate_brake(state, rule, now_wall=0.0).level == "halt"
    state = advance_equity(state, 950.0, rule)                 # dd=5% ≤ slow(8%) ⇒ 解除
    assert evaluate_brake(state, rule, now_wall=0.0).level == "none"


def test_halt_release_falls_back_to_half_when_slow_tier_off():
    rule = _rule(slow_dd_pct=0.0)
    state = _at(rule, 800.0)
    assert evaluate_brake(state, rule, now_wall=0.0).level == "halt"
    state = advance_equity(state, 910.0, rule)                 # dd=9% > 7.5% ⇒ 仍锁存
    assert evaluate_brake(state, rule, now_wall=0.0).level == "halt"
    state = advance_equity(state, 930.0, rule)                 # dd=7% ≤ 7.5% ⇒ 解除
    assert evaluate_brake(state, rule, now_wall=0.0).level == "none"


# ---------- T-15：连亏三分支 ----------
def test_loss_streak_three_branches():
    rule = _rule()
    state = initial_state(_BUDGET)
    state = note_close(state, -5.0, rule, now_wall=0.0)
    assert state.loss_streak == 1
    state = note_close(state, 0.0, rule, now_wall=0.0)         # 平局：不动
    assert state.loss_streak == 1
    state = note_close(state, 3.0, rule, now_wall=0.0)         # 盈利：归零
    assert state.loss_streak == 0


def test_loss_streak_opens_cooldown_and_resets_counter():
    rule = _rule(loss_streak=2, loss_cooldown_s=600.0)
    state = initial_state(_BUDGET)
    for _ in range(2):
        state = note_close(state, -1.0, rule, now_wall=100.0)
    assert state.loss_streak == 0 and state.cooldown_until_wall == 700.0
    view = evaluate_brake(state, rule, now_wall=200.0)
    assert view.level == "cooldown"
    assert view.reason is not None and view.reason.startswith("loss_streak_cooldown(2,")
    assert evaluate_brake(state, rule, now_wall=800.0).level == "none"   # 到期即解除


# ---------- T-14：与利润保护冷却取并集（先命中者写码，不累加）----------
def test_brake_and_profit_cooldown_are_union():
    rule = _rule(loss_streak=1, loss_cooldown_s=600.0)
    state = note_close(initial_state(_BUDGET), -1.0, rule, now_wall=0.0)
    view = evaluate_brake(state, rule, now_wall=1.0)
    decision = TradeDecision(action="open_long", size_fraction=0.5, confidence=0.6, reason="r")
    guard_cooldown = CooldownState(until_wall=1000.0, side="long",
                                   reason_code="profit_guard_trailing", label="保护平仓",
                                   scope="same_side")
    # 两层**各自独立**判定：分别作用在同一个原始决策上时，两条码都会出现。
    _, guard_alone = apply_profit_cooldown(decision, guard_cooldown, now_wall=1.0)
    _, brake_alone = apply_brake_gate(decision, view)
    assert guard_alone is not None and brake_alone is not None
    # 而在管线上串起来时是**并集而非累加**：先命中者把动作降级为 hold，后一层对 hold 直通、不再报码。
    after_guard, guard_reason = apply_profit_cooldown(decision, guard_cooldown, now_wall=1.0)
    after_brake, brake_reason = apply_brake_gate(after_guard, view)
    assert after_brake.action == "hold"
    assert guard_reason.startswith("profit_cooldown") and brake_reason is None


# ---------- T-30：阈值按杠杆定标 ----------
def test_brake_threshold_scaled_by_leverage():
    move = 0.027                                   # 标的价反向 2.7%
    for leverage, rule in ((1.0, _rule(slow_dd_pct=0.08 * 3 / 1.0, halt_dd_pct=0.15 * 3 / 1.0)),
                           (3.0, _rule(slow_dd_pct=0.08, halt_dd_pct=0.15))):
        equity = _BUDGET * (1.0 - move * leverage)   # session_pnl 落在已含杠杆的名义额上
        level = evaluate_brake(_at(rule, equity), rule, now_wall=0.0).level
        assert level == ("slow" if leverage == 3.0 else "none")
    # 3× 下若照搬无杠杆口径的 8%/15%，2.7% 的普通回调就已经踩到第一档——这正是 [P1-4] 要治的。


# ---------- 峰值口径与浮盈占比（[P1-5]）----------
def test_peak_equity_is_mark_to_market_and_share_is_observable():
    rule = _rule()
    state = advance_equity(initial_state(_BUDGET), 1200.0, rule,
                           unrealized_share=unrealized_share(180.0, 200.0))
    assert state.peak_equity == 1200.0            # 峰值随**浮盈**推进（含未实现，风控上正确）
    state = advance_equity(state, 1100.0, rule, unrealized_share=unrealized_share(90.0, 100.0))
    view = evaluate_brake(state, rule, now_wall=0.0)
    assert 0.083 < view.drawdown_pct < 0.084      # 正号口径：1 − 1100/1200
    assert view.level == "slow"                   # 保护层设计内的回吐在本层眼里就是一次真实回撤
    wire = brake_wire(view)
    assert wire["peakEquityBasis"] == "markToMarket"
    assert wire["unrealizedShare"] == 0.9         # 运维据它判断刹车是否几乎只被浮盈回吐触发
    assert unrealized_share(None, 1.0) is None    # 不可算 ⇒ None，绝不填 0


def test_brake_wire_without_rule_is_honest():
    assert brake_wire(None)["brakeLevel"] == "none"
    assert brake_wire(None)["drawdownPct"] is None
