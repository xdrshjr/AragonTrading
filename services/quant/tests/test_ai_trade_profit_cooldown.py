"""保护性平仓冷却期纯核心单测（ai-trade-profit-protection-adaptive §2.2 / T-3）。

覆盖：同向开仓被降级、`close`/`hold`/`cancel_order` 豁免（返回**同一个** decision 对象）、
`same_side` vs `both` 作用域、到期自动失效、`cooldown_s=0` 恒直通、降级码文本、prompt 渲染。
"""
from __future__ import annotations

from app.ai_auto.decider import TradeDecision
from app.ai_auto.profit_cooldown import (
    CooldownState,
    apply_profit_cooldown,
    build_cooldown_view,
    format_profit_cooldown,
)

_NOW = 1_700_000_000.0


def _state(**over) -> CooldownState:
    base = {"until_wall": _NOW + 120.0, "side": "long",
            "reason_code": "profit_guard_trailing", "label": "移动止盈"}
    base.update(over)
    return CooldownState(**base)


def _decision(action: str) -> TradeDecision:
    size = 0.5 if action in ("open_long", "open_short") else None
    return TradeDecision(action=action, size_fraction=size, confidence=0.7, reason="test")


def test_same_side_open_is_degraded_with_countdown_reason():
    """同向开仓 ⇒ 降级为 hold，降级码带剩余秒数与被锁方向（`profit_cooldown(120s,long)`）。"""
    decision, reason = apply_profit_cooldown(_decision("open_long"), _state(), now_wall=_NOW)

    assert decision.action == "hold"
    assert reason == "profit_cooldown(120s,long)"
    assert decision.reason == reason


def test_exempt_actions_return_the_same_decision_object():
    """T-3 豁免矩阵：`close` / `hold` / `cancel_order` 返回**同一个**对象（`is` 相等），绝不顺手 replace。

    这是与共识门禁、MA 通道铁律同一条铁律——需要离场时永远可以直接 `close`。
    """
    state = _state()
    for action in ("close", "hold", "cancel_order"):
        original = _decision(action)
        decision, reason = apply_profit_cooldown(original, state, now_wall=_NOW)
        assert decision is original and reason is None


def test_same_side_scope_leaves_the_opposite_direction_free():
    """默认 `same_side` 只拦被平掉的那一侧；反向开仓不受影响（冷却不是剥夺交易能力）。"""
    original = _decision("open_short")
    decision, reason = apply_profit_cooldown(original, _state(side="long"), now_wall=_NOW)
    assert decision is original and reason is None

    both = _state(side="long", scope="both")
    blocked, blocked_reason = apply_profit_cooldown(_decision("open_short"), both, now_wall=_NOW)
    assert blocked.action == "hold" and blocked_reason.startswith("profit_cooldown(")


def test_cooldown_expires_purely_by_time():
    """纯时间驱动、无清理任务：`now >= until` 即视为未激活，视图消失、门禁直通。"""
    state = _state(until_wall=_NOW + 30.0)
    original = _decision("open_long")

    decision, reason = apply_profit_cooldown(original, state, now_wall=_NOW + 30.0)
    assert decision is original and reason is None
    assert build_cooldown_view(state, now_wall=_NOW + 30.0) is None
    assert build_cooldown_view(None, now_wall=_NOW) is None


def test_view_carries_remaining_seconds_and_scope():
    """wire 视图六键；`remainingSeconds` 是下发瞬间快照值（前端只用于显示，不据它判真伪）。"""
    view = build_cooldown_view(_state(), now_wall=_NOW + 45.0)

    assert view == {"activeUntilTs": int(_NOW + 120.0), "remainingSeconds": 75,
                    "scope": "same_side", "blockedSide": "long",
                    "reasonCode": "profit_guard_trailing", "label": "移动止盈"}


def test_prompt_section_states_remaining_time_and_exemptions():
    """§2.2.3：冷却段必须同时给出剩余时间与豁免范围——只说「被拦截」会让模型以为交易能力被剥夺。"""
    assert format_profit_cooldown(None) == ""

    text = format_profit_cooldown(build_cooldown_view(_state(), now_wall=_NOW))
    assert "冷却期" in text and "120s" in text
    assert "做多开仓会被系统拦截" in text and "做空不受影响" in text
    assert "`close`" in text and "`cancel_order`" in text
