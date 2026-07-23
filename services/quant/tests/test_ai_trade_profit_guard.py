"""利润保护层纯核心单测（ai-trade-profit-protection §7.1/§7.2）。

覆盖：三层解析语义、epoch 推进与峰值单调、多空对称、三条规则优先级、最短持有期、fail-open 全分支、
退出线反解、文案渲染，以及**必须先写的复现用例**：
- T-1「DOGE R74→R91 回放」：断言在浮盈仍为正的那一跳即触发移动止盈，此后的负浮盈点位永不被到达。
- T-2「翻负前保本」：峰值未达武装阈值但过了保本阈值，价格回落穿越保本线即离场，结算浮盈 ≥ 0。

[P1-3] 全部断言写成**构造式**（由 entry/peak/giveback/leverage 反算），不照抄任何字面价格——设计文档
v1 的样例数字曾四处互相矛盾，照抄即把矛盾固化进测试。
"""
from __future__ import annotations

import math

import pytest

from app.ai_auto.profit_guard import (
    ARMED_STATUSES,
    REASON_BREAKEVEN,
    REASON_HARD_TAKE_PROFIT,
    REASON_TRAILING,
    STATUS_ARMED,
    STATUS_BREAKEVEN_ARMED,
    STATUS_IDLE,
    STATUS_UNAVAILABLE,
    PositionFacts,
    ProfitGuardRule,
    advance_state,
    build_idle_snapshot,
    build_snapshot,
    evaluate_profit_guard,
    format_profit_guard_charter,
    format_profit_guard_for_prompt,
    format_profit_hold_note,
    resolve_profit_guard_rule,
    rule_wire,
    unavailable_reason_for,
)
from app.config import Settings

# 诊断样本 DOGE run 1561 的真实断面（R74 在 0.07245 开多，5× 杠杆）。
_ENTRY = 0.07245
_LEV = 5.0
# 币量取自「峰值浮盈 = +0.302 USDT ⇔ 峰值保证金收益 ≈ 5.18%」这条约束（见下方 _peak_px_for 反算）。
_QTY = 402.7
_WALL = 1_700_000_000.0
_STEP = 60.0            # > min_hold_s(30) ⇒ 自第二个点起保护层已过最短持有期


def _rule(**kwargs) -> ProfitGuardRule:
    return ProfitGuardRule(**kwargs)


def _facts(px: float, *, now_wall: float, seq: int = 1, side: str = "long",
           entry: float = _ENTRY, qty: float = _QTY, lev: float = _LEV,
           **extra) -> PositionFacts:
    return PositionFacts(seq=seq, side=side, entry_px=entry, leverage=lev, qty_coin=qty,
                         mark_px=px, now_wall=now_wall, **extra)


def _peak_px_for(return_pct: float, *, side: str = "long", entry: float = _ENTRY,
                 lev: float = _LEV) -> float:
    """由目标峰值**保证金收益率**反算峰值价（构造式基准，禁止在用例里写死价格）。"""
    move = return_pct / lev
    return entry * (1 + move) if side == "long" else entry * (1 - move)


def _trail_px_for(peak_px: float, rule: ProfitGuardRule, *, side: str = "long",
                  entry: float = _ENTRY) -> float:
    """移动止盈线 = 入场价 + 方向化的「峰值距离 ×（1 − 允许回吐比例）」。"""
    distance = abs(peak_px - entry) * (1 - rule.giveback_pct)
    return entry + distance if side == "long" else entry - distance


def _breakeven_px_for(rule: ProfitGuardRule, *, side: str = "long", entry: float = _ENTRY,
                      lev: float = _LEV) -> float:
    offset = rule.breakeven_offset_return_pct / lev
    return entry * (1 + offset) if side == "long" else entry * (1 - offset)


def _walk(prices, rule: ProfitGuardRule, *, side: str = "long", entry: float = _ENTRY,
          qty: float = _QTY):
    """按价格序列逐点推进状态并判定；**首个触发点即终止**（此后的点位永不被到达）。

    Returns:
        `(visited, snapshot, verdict)`——`visited` 为实际走到的价格前缀，未触发时 verdict 为 None。
    """
    state = None
    visited: list[float] = []
    snapshot: dict | None = None
    for index, px in enumerate(prices):
        facts = _facts(px, now_wall=_WALL + index * _STEP, side=side, entry=entry, qty=qty)
        state = advance_state(state, facts)
        snapshot = build_snapshot(rule, state, facts)
        visited.append(px)
        verdict = evaluate_profit_guard(snapshot, rule)
        if verdict is not None:
            return visited, snapshot, verdict
    return visited, snapshot, None


# ---------------- T-1：DOGE R74→R91 回放（先写的复现用例）----------------
def test_doge_replay_locks_profit_while_still_positive():
    """T-1：峰值 +0.302 后一路回吐，保护层必须在**浮盈仍为正**的那一跳平仓，绝不走到翻负点位。"""
    rule = _rule()
    peak_px = _peak_px_for(0.0518)                     # ≈ +0.302 USDT，达 arm 5%
    trail_px = _trail_px_for(peak_px, rule)
    # R74 开多 → R78 峰值 → R80 小幅回吐 → 触及移动止盈线 → （R88/R91 的负浮盈点位不应被到达）
    negative_points = [_ENTRY * 0.999, _ENTRY * 0.995]
    prices = [_ENTRY, peak_px, _peak_px_for(0.0414), trail_px, *negative_points]

    visited, snapshot, verdict = _walk(prices, rule)

    assert verdict is not None and verdict.reason_code == REASON_TRAILING
    assert visited[-1] == pytest.approx(trail_px)      # 恰在构造式反算的触发点终止
    assert all(px not in visited for px in negative_points)
    assert snapshot["unrealizedPnlUsd"] > 0            # 锁定的是**正**浮盈，不是翻负后的止损
    assert snapshot["peakUnrealizedPnlUsd"] == pytest.approx(0.302, abs=0.005)
    assert verdict.note["pnlUsd"] == snapshot["unrealizedPnlUsd"]


def test_doge_replay_exit_beats_final_realized_loss():
    """T-1 续：保护层锁定的浮盈，必须优于真实运行记录里最终成交的 −0.108 USDT。"""
    rule = _rule()
    peak_px = _peak_px_for(0.0518)
    _, snapshot, verdict = _walk([_ENTRY, peak_px, _trail_px_for(peak_px, rule)], rule)

    assert verdict is not None
    assert snapshot["unrealizedPnlUsd"] > -0.108


# ---------------- T-2：翻负前保本 ----------------
def test_breakeven_exits_before_unrealized_turns_negative():
    """T-2：峰值 3.2%（未达 arm 5% 但过保本 3%），跌回保本线即离场，结算浮盈 ≥ 0。"""
    rule = _rule()
    peak_px = _peak_px_for(0.032)
    breakeven_px = _breakeven_px_for(rule)

    visited, snapshot, verdict = _walk([_ENTRY, peak_px, breakeven_px], rule)

    assert verdict is not None and verdict.reason_code == REASON_BREAKEVEN
    assert visited[-1] == pytest.approx(breakeven_px)
    assert snapshot["trailStopPx"] is None             # 移动止盈未武装（峰值未达 arm）
    assert snapshot["status"] == STATUS_BREAKEVEN_ARMED
    assert snapshot["unrealizedPnlUsd"] >= 0


def test_breakeven_not_armed_below_threshold():
    """峰值连保本阈值都没到 ⇒ 两条线都不武装 ⇒ 价格跌回入场价也零动作（fail-open 的正面语义）。"""
    rule = _rule()
    _, snapshot, verdict = _walk([_ENTRY, _peak_px_for(0.01), _ENTRY * 0.999], rule)

    assert verdict is None
    assert snapshot["status"] == STATUS_IDLE
    assert snapshot["effectiveExitPx"] is None


# ---------------- epoch 生命周期与峰值单调 ----------------
def test_peak_advances_monotonically_within_epoch():
    state = None
    for index, px in enumerate([_ENTRY, _ENTRY * 1.02, _ENTRY * 1.01]):
        state = advance_state(state, _facts(px, now_wall=_WALL + index))

    assert state.peak_px == pytest.approx(_ENTRY * 1.02)   # 回落不下修峰值


def test_new_seq_starts_fresh_epoch_even_when_side_unchanged():
    """[P0-1] 同向重开必须是新 epoch：旧峰值 / 旧持仓起点绝不被继承（否则新仓开出即被强平）。"""
    state = advance_state(None, _facts(_ENTRY * 1.05, now_wall=_WALL, seq=1))
    assert state.peak_px == pytest.approx(_ENTRY * 1.05)

    reopened = advance_state(state, _facts(_ENTRY, now_wall=_WALL + 999, seq=2))

    assert reopened.seq == 2
    assert reopened.peak_px == pytest.approx(_ENTRY)       # 峰值从新仓入场价重新起算
    assert reopened.opened_wall == _WALL + 999             # 持仓时长从 0 起算


def test_side_change_starts_fresh_epoch():
    state = advance_state(None, _facts(_ENTRY * 1.05, now_wall=_WALL, seq=1))
    flipped = advance_state(state, _facts(_ENTRY, now_wall=_WALL + 1, seq=1, side="short"))

    assert flipped.side == "short" and flipped.peak_px == pytest.approx(_ENTRY)


# ---------------- 多空对称 ----------------
def test_short_side_is_fully_symmetric():
    rule = _rule()
    peak_px = _peak_px_for(0.0518, side="short")
    trail_px = _trail_px_for(peak_px, rule, side="short")
    assert peak_px < _ENTRY and trail_px > peak_px         # 做空的峰值在下方、退出线在其上方

    visited, snapshot, verdict = _walk([_ENTRY, peak_px, trail_px], rule, side="short")

    assert verdict is not None and verdict.reason_code == REASON_TRAILING
    assert visited[-1] == pytest.approx(trail_px)
    assert snapshot["unrealizedPnlUsd"] > 0
    assert snapshot["effectiveExitPx"] == pytest.approx(trail_px)


# ---------------- 优先级与最短持有期 ----------------
def test_hard_take_profit_wins_over_trailing():
    rule = _rule(hard_tp_return_pct=0.04)
    facts = _facts(_peak_px_for(0.05), now_wall=_WALL + 100)
    state = advance_state(None, _facts(_ENTRY, now_wall=_WALL))
    state = advance_state(state, facts)

    verdict = evaluate_profit_guard(build_snapshot(rule, state, facts), rule)

    assert verdict is not None and verdict.reason_code == REASON_HARD_TAKE_PROFIT


def test_min_hold_blocks_every_trigger():
    """开仓瞬间的价差 / 一跳噪音绝不打掉刚建的仓（最短持有期，§2.4 第 1 条）。"""
    rule = _rule()
    peak_px = _peak_px_for(0.0518)
    state = advance_state(None, _facts(peak_px, now_wall=_WALL))
    facts = _facts(_trail_px_for(peak_px, rule), now_wall=_WALL + rule.min_hold_s - 1)
    state = advance_state(state, facts)

    snapshot = build_snapshot(rule, state, facts)
    assert snapshot["effectiveExitPx"] is not None          # 线已武装（如实展示）
    assert evaluate_profit_guard(snapshot, rule) is None    # 但最短持有期内不动作


# ---------------- fail-open 前置豁免 ----------------
@pytest.mark.parametrize("kwargs,expected", [
    ({"px": float("nan")}, "no_price"),
    ({"px": 0.0}, "no_price"),
    ({"qty": 0.0}, "bad_position"),
    ({"entry": 0.0}, "bad_position"),
    ({"lev": 0.0}, "bad_position"),
])
def test_unavailable_reason_covers_every_bad_reading(kwargs, expected):
    px = kwargs.pop("px", _ENTRY)
    facts = _facts(px, now_wall=_WALL, **kwargs)
    assert unavailable_reason_for(facts, _rule(), px_age_s=0.0, stale_feed=False) == expected


def test_stale_price_is_unavailable_and_renders_no_exit_line():
    """[P1-6] 判定价超龄 ⇒ status=unavailable、退出线一律 None、文案不含任何退出价。"""
    rule = _rule()
    peak_px = _peak_px_for(0.0518)
    state = advance_state(None, _facts(peak_px, now_wall=_WALL))
    facts = _facts(_trail_px_for(peak_px, rule), now_wall=_WALL + 500,
                   px_age_s=rule.max_px_age_s + 1)
    reason = unavailable_reason_for(facts, rule, px_age_s=facts.px_age_s, stale_feed=False)
    assert reason == "stale_feed"

    snapshot = build_snapshot(rule, state, facts, unavailable_reason=reason)

    assert snapshot["status"] == STATUS_UNAVAILABLE
    assert snapshot["unavailableReason"] == "stale_feed"
    assert all(snapshot[key] is None for key in
               ("trailStopPx", "breakevenPx", "hardTakeProfitPx", "effectiveExitPx"))
    assert evaluate_profit_guard(snapshot, rule) is None
    text = format_profit_guard_for_prompt(snapshot)
    assert "当前不可用" in text and "有效退出线" not in text


@pytest.mark.parametrize("oco, expects_backstop", [
    ({"exchange_tp_px": _ENTRY * 1.10, "exchange_sl_px": _ENTRY * 0.95,
      "exchange_oco_source": "trade"}, True),
    ({}, False),                       # 运维把 AI_TRADE_DEFAULT_SL_PCT/_TP_PCT 置 0 ⇒ 交易所侧没挂单
])
def test_unavailable_text_only_claims_a_backstop_that_actually_exists(oco, expects_backstop):
    """保护层停手时不得宣称一条并不存在的兜底（§六.5 诚实约束）。

    `manager._resolve_risk` 的 `sl or None` 使「两个 pct 都为 0 ⇒ 完全不挂 OCO」可达。彼时若照写
    「仅交易所侧止盈止损仍生效」，模型会在**既没有保护层、又没有交易所兜底**的断面上以为自己有防线
    ——这是 P1-5 道德风险的最坏形态。
    """
    rule = _rule()
    facts = _facts(_ENTRY, now_wall=_WALL, px_age_s=rule.max_px_age_s + 1, **oco)
    state = advance_state(None, facts)
    snapshot = build_snapshot(rule, state, facts, unavailable_reason="stale_feed")

    text = format_profit_guard_for_prompt(snapshot)

    assert "当前不可用" in text
    assert ("仍生效" in text) is expects_backstop
    if not expects_backstop:
        assert "没有任何自动兜底" in text


def test_degraded_feed_is_unavailable_even_when_price_is_fresh():
    facts = _facts(_ENTRY, now_wall=_WALL)
    assert unavailable_reason_for(facts, _rule(), px_age_s=0.0, stale_feed=True) == "stale_feed"


def test_evaluate_is_inert_without_rule_or_snapshot():
    snapshot = build_snapshot(_rule(), advance_state(None, _facts(_ENTRY, now_wall=_WALL)),
                              _facts(_ENTRY, now_wall=_WALL))
    assert evaluate_profit_guard(snapshot, None) is None
    assert evaluate_profit_guard(snapshot, _rule(enabled=False)) is None
    assert evaluate_profit_guard(None, _rule()) is None
    assert evaluate_profit_guard(build_idle_snapshot(_rule()), _rule()) is None


# ---------------- 三层解析语义 ----------------
def test_resolve_defaults_to_enabled_when_field_absent():
    rule = resolve_profit_guard_rule(None, Settings())
    assert rule is not None and rule.enabled
    assert rule.arm_return_pct == Settings().ai_trade_profit_guard_arm_return_pct


def test_resolve_returns_none_when_explicitly_disabled():
    assert resolve_profit_guard_rule({"enabled": False}, Settings()) is None


def test_resolve_returns_none_when_ops_switch_off():
    settings = Settings(ai_trade_profit_guard_enabled=False)
    assert resolve_profit_guard_rule({"enabled": True, "armReturnPct": 0.1}, settings) is None


def test_resolve_fills_only_missing_fields():
    rule = resolve_profit_guard_rule({"armReturnPct": 0.2, "minHoldSeconds": 0}, Settings())
    assert rule.arm_return_pct == 0.2
    assert rule.min_hold_s == 0.0                       # 显式 0 被保留（不是「缺省」）
    assert rule.giveback_pct == Settings().ai_trade_profit_guard_giveback_pct


def test_hard_take_profit_null_and_zero_are_equivalent():
    """[P2-1] 请求体的 null 与 0 均表关闭，解析点统一归一为 0.0，此后不可再分辨。"""
    from_null = resolve_profit_guard_rule({"hardTakeProfitReturnPct": None}, Settings())
    from_zero = resolve_profit_guard_rule({"hardTakeProfitReturnPct": 0}, Settings())
    assert from_null.hard_tp_return_pct == from_zero.hard_tp_return_pct == 0.0


def test_rule_wire_mirrors_rule_and_is_none_when_unarmed():
    assert rule_wire(None) is None
    assert rule_wire(_rule(enabled=False)) is None
    wire = rule_wire(_rule())
    assert wire["enabled"] is True and wire["givebackPct"] == 0.40


# ---------------- 文案渲染 ----------------
def test_charter_keeps_both_mandated_semantics():
    """[P1-5] 宪章两层语义不可删：① 被强平 = 决策失误；② 保护层可能不可用、不可依赖。"""
    text = format_profit_guard_charter(_rule())
    assert "决策失误" in text
    assert "不可用" in text and "不是一条你可以依赖的保证" in text
    assert format_profit_guard_charter(None) == ""      # 未装配 ⇒ 空串垫片（渲染逐字节不变）
    assert format_profit_guard_charter(_rule(enabled=False)) == ""


def test_prompt_section_renders_peak_giveback_hold_and_effective_exit():
    """验收 #6：持仓时 prompt 必须同时含峰值浮盈、回吐比例、持仓时长与**有效退出线**四项。"""
    rule = _rule()
    peak_px = _peak_px_for(0.0518)
    state = advance_state(None, _facts(_ENTRY, now_wall=_WALL))
    facts = _facts(_peak_px_for(0.0414), now_wall=_WALL + 930, bars_in_position=4,
                   exchange_tp_px=_ENTRY * 1.10, exchange_sl_px=_ENTRY * 0.95,
                   exchange_oco_source="trade")
    state = advance_state(state, _facts(peak_px, now_wall=_WALL + 60))
    state = advance_state(state, facts)
    snapshot = build_snapshot(rule, state, facts)

    text = format_profit_guard_for_prompt(snapshot)

    assert "峰值浮盈" in text and "已回吐" in text and "持仓 930s" in text
    assert "有效退出线" in text and "（4 根）" in text
    assert f"{snapshot['effectiveExitPx']:.6f}" in text
    assert "交易所侧兜底：" in text and "（估算）" not in text   # 取自成交回执 ⇒ 不标估算
    assert snapshot["status"] == STATUS_ARMED and snapshot["status"] in ARMED_STATUSES


def test_prompt_section_marks_derived_exchange_oco_as_estimated():
    rule = _rule()
    facts = _facts(_ENTRY, now_wall=_WALL, exchange_tp_px=_ENTRY * 1.1,
                   exchange_sl_px=_ENTRY * 0.95, exchange_oco_source="derived")
    snapshot = build_snapshot(rule, advance_state(None, facts), facts)
    assert "（估算）" in format_profit_guard_for_prompt(snapshot)


def test_prompt_section_degrades_honestly_when_not_armed():
    rule = _rule()
    facts = _facts(_ENTRY, now_wall=_WALL + 100)
    snapshot = build_snapshot(rule, advance_state(None, facts), facts)

    text = format_profit_guard_for_prompt(snapshot)

    assert "尚未达到武装阈值" in text
    assert "尚未武装，本轮保护层不会平仓" in text


def test_flat_snapshot_only_carries_last_exit_receipt():
    snapshot = build_idle_snapshot(_rule())
    assert format_profit_guard_for_prompt(snapshot) == ""
    snapshot["lastExit"] = {"label": "移动止盈", "exitPx": _ENTRY, "pnlUsd": 0.18,
                            "peakPnlUsd": 0.30}
    text = format_profit_guard_for_prompt(snapshot)
    assert "上一笔持仓已由利润保护层强制平仓" in text and "移动止盈" in text


def test_prompt_section_is_empty_without_snapshot():
    assert format_profit_guard_for_prompt(None) == ""
    assert format_profit_guard_for_prompt({}) == ""


def test_profit_hold_note_is_symmetric_and_gated_on_positive_pnl():
    rule = _rule()
    facts = _facts(_peak_px_for(0.04), now_wall=_WALL + 100)
    profit = build_snapshot(rule, advance_state(None, _facts(_ENTRY, now_wall=_WALL)), facts)

    note = format_profit_hold_note(profit, 3)

    assert "你的退出线" in note
    assert "这不是必须平仓的指令" in note                # 措辞对称：不得变成强制平仓指令
    assert "必须平仓" in note and "立即平仓" not in note
    assert format_profit_hold_note(profit, 0) == ""     # 阈值未达 / 关闭 ⇒ 整段省略
    assert format_profit_hold_note(None, 3) == ""


def test_profit_hold_note_suppressed_when_position_is_losing():
    rule = _rule()
    facts = _facts(_ENTRY * 0.99, now_wall=_WALL + 100)
    losing = build_snapshot(rule, advance_state(None, _facts(_ENTRY, now_wall=_WALL)), facts)
    assert losing["returnPct"] < 0
    assert format_profit_hold_note(losing, 5) == ""


# ---------------- 退出线反解与数值自洽 ----------------
def test_snapshot_numbers_are_internally_consistent():
    """[P1-3] 快照内部逐项自洽：收益率 = 价格变动 × 杠杆；回吐比例与峰值 / 当前收益率反算一致。"""
    rule = _rule()
    peak_px = _peak_px_for(0.0518)
    state = advance_state(None, _facts(_ENTRY, now_wall=_WALL))
    state = advance_state(state, _facts(peak_px, now_wall=_WALL + 60))
    facts = _facts(_peak_px_for(0.0414), now_wall=_WALL + 120)
    state = advance_state(state, facts)

    snap = build_snapshot(rule, state, facts)

    assert snap["returnPct"] == pytest.approx(snap["priceMovePct"] * _LEV, rel=1e-6)
    assert snap["peakReturnPct"] == pytest.approx(0.0518, abs=1e-4)
    expected_giveback = (snap["peakReturnPct"] - snap["returnPct"]) / snap["peakReturnPct"]
    assert snap["givebackRatio"] == pytest.approx(expected_giveback, rel=1e-4)
    assert snap["trailStopPx"] == pytest.approx(_trail_px_for(peak_px, rule))
    assert snap["breakevenPx"] == pytest.approx(_breakeven_px_for(rule))
    assert snap["effectiveExitPx"] == pytest.approx(max(snap["trailStopPx"],
                                                        snap["breakevenPx"]))
    assert math.isfinite(snap["unrealizedPnlUsd"])
