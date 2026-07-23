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
    parse_giveback_tiers,
    resolve_giveback_pct,
    resolve_profit_guard_rule,
    rule_wire,
    safe_atr,
    sanitize_atr,
    unavailable_reason_for,
)
from app.ai_auto.profit_guard_text import (
    describe_exit_outcome,
    format_profit_guard_charter,
    format_profit_guard_for_prompt,
    format_profit_hold_note,
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


# ==================== F1 自适应（ai-trade-profit-protection-adaptive §2.1）====================
def _snapshot_at(peak_return_pct: float, mark_px: float, rule: ProfitGuardRule, *,
                 atr_px: float | None = None, side: str = "long") -> dict:
    """把状态推到指定峰值收益后，用给定判定价组装一份快照（全部构造式，禁止写死价格）。"""
    peak_px = _peak_px_for(peak_return_pct, side=side)
    state = advance_state(None, _facts(_ENTRY, now_wall=_WALL, side=side))
    state = advance_state(state, _facts(peak_px, now_wall=_WALL + _STEP, side=side))
    facts = _facts(mark_px, now_wall=_WALL + 2 * _STEP, side=side, atr_px=atr_px)
    state = advance_state(state, facts)
    return build_snapshot(rule, state, facts)


def test_giveback_tiers_tighten_as_profit_grows():
    """验收 #1：峰值收益跨过 10% / 25% / 50% 三档，有效回吐阈立即变为 0.35 / 0.28 / 0.20。"""
    tiers = parse_giveback_tiers("0.10:0.35,0.25:0.28,0.50:0.20")
    rule = _rule(giveback_tiers=tiers)

    assert resolve_giveback_pct(rule, 0.05) == rule.giveback_pct    # 无匹配档 → 基准值
    assert resolve_giveback_pct(rule, 0.10) == 0.35                 # 边界含等于
    assert resolve_giveback_pct(rule, 0.30) == 0.28
    assert resolve_giveback_pct(rule, 0.80) == 0.20

    snap = _snapshot_at(0.30, _peak_px_for(0.20), rule)
    assert snap["givebackPctEffective"] == 0.28
    # 线由同一个有效阈反算：trail = entry + 峰值距离 ×（1 − 0.28）
    span = abs(_peak_px_for(0.30) - _ENTRY)
    assert snap["trailStopPx"] == pytest.approx(_ENTRY + span * (1 - 0.28))
    assert snap["trailBasis"] == "giveback"


def test_giveback_tiers_reject_non_monotonic_config():
    """R-2：阈值必须严格递增、回吐比例必须单调不增——写反等于把本轮要修的 bug 写进配置。"""
    with pytest.raises(ValueError):
        parse_giveback_tiers("0.10:0.30,0.25:0.40")        # 赚得越多却允许回吐更多
    with pytest.raises(ValueError):
        parse_giveback_tiers("0.25:0.30,0.10:0.20")        # 阈值不递增
    with pytest.raises(ValueError):
        parse_giveback_tiers("0:0.30")                     # 首档阈值须 > 0
    with pytest.raises(ValueError):
        parse_giveback_tiers(",".join(f"0.{i}:0.3" for i in range(1, 9)))   # 超 6 档
    assert parse_giveback_tiers("") == () and parse_giveback_tiers(None) == ()


def test_atr_bounds_are_tri_state():
    """验收 #2：ATR 下界抬宽 / 上界压紧 / 不绑定三态，`trailBasis` 如实记录是谁定住了这条线。"""
    peak_return = 0.0518
    span = abs(_peak_px_for(peak_return) - _ENTRY)
    mark = _peak_px_for(0.05)
    # 不绑定：ATR 极小 ⇒ 距离仍由回吐比例决定。
    loose = _rule(atr_floor_mult=0.8, atr_cap_mult=3.0)
    snap = _snapshot_at(peak_return, mark, loose, atr_px=span * 0.25)
    assert snap["trailBasis"] == "giveback"      # 0.8×ATR < 0.4×span < 3×ATR ⇒ 两界都不绑定
    assert snap["trailDistancePx"] == pytest.approx(span * loose.giveback_pct, abs=1e-8)
    # 下界绑定：0.8×ATR 大于按比例算出的距离 ⇒ 线被放宽（远离峰值）。
    floored = _snapshot_at(peak_return, mark, loose, atr_px=span)
    assert floored["trailBasis"] == "atr_floor"
    assert floored["trailDistancePx"] == pytest.approx(0.8 * span, abs=1e-8)
    # 上界绑定：3×ATR 小于按比例算出的距离 ⇒ 线被收紧。
    capped = _snapshot_at(peak_return, mark, loose, atr_px=span * 0.05)
    assert capped["trailBasis"] == "atr_cap"
    assert capped["trailDistancePx"] == pytest.approx(3.0 * span * 0.05, abs=1e-8)
    # ATR 不可用 ⇒ 上下界整体不生效（fail-open 降级为纯阶梯回吐）。
    degraded = _snapshot_at(peak_return, mark, loose, atr_px=None)
    assert degraded["trailBasis"] == "giveback" and degraded["atrPx"] is None


def test_trail_clamps_to_breakeven_when_atr_floor_pushes_below_entry():
    """T-2（§2.1.4 断面 C）：ATR 下界把线推到保本线之下 ⇒ 夹到保本线，且区间内仍不触发。"""
    peak_return = 0.0587
    span = abs(_peak_px_for(peak_return) - _ENTRY)
    rule = _rule(atr_floor_mult=0.8, atr_cap_mult=3.0)
    # ATR 大到让 0.8×ATR > 峰值距离本身 ⇒ 原始 trail 落到入场价之下。
    snap = _snapshot_at(peak_return, _peak_px_for(0.05), rule, atr_px=span * 2)

    breakeven = _breakeven_px_for(rule)
    assert snap["breakevenPx"] == pytest.approx(breakeven)
    assert snap["trailStopPx"] == pytest.approx(breakeven)
    assert snap["trailBasis"] == "clamped_breakeven"
    assert snap["trailClampedAtEntry"] is True
    assert snap["effectiveExitPx"] == pytest.approx(breakeven)
    # 距离按**实际那条线**回算（RV-2）：三键自洽 ⇒ peak − trailDistancePx 恰是 trailStopPx，
    # 否则 prompt 的「距峰值 X」会让人反算出一条不存在的线。
    assert snap["trailDistancePx"] == pytest.approx(_peak_px_for(peak_return) - breakeven, abs=1e-8)
    # 现价仍在 (保本线, 峰值) 之间 ⇒ 不触发（夹紧不制造额外的触发点）。
    assert evaluate_profit_guard(snap, rule) is None


def test_trail_suppressed_at_entry_never_forces_a_losing_exit():
    """T-5（§2.1.4 断面 D / P0-1 守门）：保本线关闭 + ATR 下界绑定 ⇒ 整条线作废，压到入场价之下仍不触发。

    若实现写成「夹到 entry 后照常触发」，本用例必红——而线上的表现会是一次 `pnl = −手续费` 的强平，
    配上「锁定利润」的文案。
    """
    peak_return = 0.0587
    span = abs(_peak_px_for(peak_return) - _ENTRY)
    rule = _rule(breakeven_arm_return_pct=0.0, atr_floor_mult=0.8, atr_cap_mult=3.0)

    snap = _snapshot_at(peak_return, _peak_px_for(0.05), rule, atr_px=span * 2)
    assert snap["trailStopPx"] is None
    assert snap["trailBasis"] == "suppressed_at_entry"
    assert snap["trailClampedAtEntry"] is True
    assert snap["trailDistancePx"] is None
    assert snap["effectiveExitPx"] is None
    # 有效阈与 ATR 仍如实下发（用户要能看出「是波动率把线推到了入场价之下」）。
    assert snap["givebackPctEffective"] == rule.giveback_pct and snap["atrPx"] is not None

    for mark in (_ENTRY, _ENTRY * 0.99):
        pressed = _snapshot_at(peak_return, mark, rule, atr_px=span * 2)
        assert evaluate_profit_guard(pressed, rule) is None

    # 同一构造恢复保本线 ⇒ 退回 T-2 的 clamped_breakeven 分支（抑制只在没有兜底线时发生）。
    restored = _snapshot_at(peak_return, _peak_px_for(0.05),
                            _rule(breakeven_arm_return_pct=0.03, atr_floor_mult=0.8,
                                  atr_cap_mult=3.0), atr_px=span * 2)
    assert restored["trailBasis"] == "clamped_breakeven"


def test_allow_filter_restricts_which_rules_may_fire():
    """`allow=` 白名单（F3 保守成交序用）：不在名单内的规则即便命中也不裁决；默认 None = 全开。"""
    rule = _rule(hard_tp_return_pct=0.20)
    peak_return = 0.0518
    trail_px = _trail_px_for(_peak_px_for(peak_return), rule)
    snap = _snapshot_at(peak_return, trail_px, rule)

    assert evaluate_profit_guard(snap, rule).reason_code == REASON_TRAILING
    assert evaluate_profit_guard(snap, rule, allow=(REASON_TRAILING,)) is not None
    assert evaluate_profit_guard(snap, rule, allow=(REASON_HARD_TAKE_PROFIT,)) is None


def test_unavailable_snapshot_nulls_every_new_key():
    """§2.1.3：`unavailable` 时新增五键与三条线同待遇——一律 None，绝不下发不作数的数字。"""
    rule = _rule(atr_floor_mult=0.8)
    facts = _facts(_peak_px_for(0.05), now_wall=_WALL + _STEP, atr_px=0.001)
    state = advance_state(None, _facts(_ENTRY, now_wall=_WALL))
    snap = build_snapshot(rule, state, facts, unavailable_reason="stale_feed")

    assert snap["status"] == STATUS_UNAVAILABLE
    for key in ("givebackPctEffective", "trailBasis", "trailDistancePx",
                "trailClampedAtEntry", "atrPx"):
        assert snap[key] is None


def test_default_rule_is_byte_identical_to_v1_trail():
    """G-1：`ProfitGuardRule()` 直构造（阶梯空 / atr_*=0）⇒ 移动止盈线与 v1 公式逐字节相等。"""
    rule = _rule()
    peak_px = _peak_px_for(0.0518)
    snap = _snapshot_at(0.0518, _peak_px_for(0.04), rule, atr_px=0.01)   # ATR 有值也不生效

    assert rule.giveback_tiers == () and rule.atr_floor_mult == 0.0 and rule.cooldown_s == 0.0
    assert snap["trailStopPx"] == pytest.approx(_trail_px_for(peak_px, rule))
    assert snap["trailBasis"] == "giveback" and snap["trailClampedAtEntry"] is False


def test_exit_outcome_copy_is_derived_from_pnl_sign():
    """§六.1b / P0-1：措辞由 pnl 符号派生——绝不在一笔非盈利出场上宣称「锁定利润」。"""
    assert "锁定利润" in describe_exit_outcome(1.25)
    assert "锁定利润" not in describe_exit_outcome(0.0)
    assert "锁定利润" not in describe_exit_outcome(-0.42)
    assert "锁定利润" not in describe_exit_outcome(None)


def test_charter_discloses_adaptive_and_cooldown_only_when_active():
    """宪章的两段新披露：未启用（v1 默认）⇒ 逐字回退；启用 ⇒ 必须说清「以每轮输入为准」与冷却豁免。"""
    assert "保护线是自适应的" not in format_profit_guard_charter(_rule())
    assert "冷却期" not in format_profit_guard_charter(_rule())

    adaptive = format_profit_guard_charter(_rule(
        giveback_tiers=parse_giveback_tiers("0.10:0.35"), atr_floor_mult=0.8,
        cooldown_s=180.0))
    assert "保护线是自适应的" in adaptive and "有效回吐阈" in adaptive
    assert "180s" in adaptive and "不受任何限制" in adaptive


def test_safe_atr_degrades_instead_of_guessing():
    """ATR 取数：样本不足 / NaN / ≤0 一律 None（与回测侧同一条判据 sanitize_atr）。"""
    import pandas as pd

    assert sanitize_atr(float("nan")) is None
    assert sanitize_atr(0.0) is None and sanitize_atr(-1.0) is None
    assert sanitize_atr("x") is None and sanitize_atr(0.5) == 0.5

    rows = [{"ts": i, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0, "vol": 1.0}
            for i in range(30)]
    df = pd.DataFrame(rows)
    assert safe_atr(df, 14) == pytest.approx(2.0, rel=1e-6)
    assert safe_atr(df.iloc[:5], 14) is None            # 样本不足 ⇒ 降级
    assert safe_atr(None, 14) is None
