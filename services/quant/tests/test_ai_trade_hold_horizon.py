"""持仓周期铁律层纯核心单测（ai-trade-holding-horizon-modes §6.1 用例 H-1 ~ H-26）。

纯函数、无网络、无 LLM、无 broker：构造断面 → 快照 → 三道闸判定。四条被冻结的不变式各有专项用例
把守：**只约束主动换手**（H-15/H-16/H-17 豁免矩阵）、**fail-open**（H-10/H-12/H-13）、
**墙钟不是根数**（H-3/H-20）、**锁必须有钥匙**（H-22/H-23 的确认窗口上界）。
"""
from __future__ import annotations

import pytest

from app.ai_auto.cost_model import resolve_cost_params
from app.ai_auto.decider import TradeDecision
from app.ai_auto.hold_horizon import (
    PROFILES,
    HorizonFacts,
    HorizonRule,
    HorizonState,
    build_snapshot,
    derive_confirm_window_s,
    derive_expected_hold_bars,
    derive_min_hold_s,
    evaluate_horizon_gate,
    gate_status,
    gated_kind,
    resolve_horizon_rule,
    rule_wire,
)
from app.ai_auto.hold_horizon_profiles import profile_wire, profiles_wire
from app.config import Settings

_NOW = 1_700_000_000.0


def _settings(**over) -> Settings:
    return Settings(**over)


def _rule(**over) -> HorizonRule:
    """`intraday_swing` 口径的规则（min_hold=2h、确认 2 轮、迟滞 1.5×、确认窗口 1h）。"""
    base = {"min_hold_s": 7200.0, "exit_confirm_rounds": 2, "confirm_window_s": 3600.0,
            "hysteresis_k": 1.5, "target_hold_min_s": 14400.0, "target_hold_max_s": 43200.0,
            "expected_hold_bars": 12, "title": "日内波段", "target_label": "4 – 12 小时"}
    return HorizonRule(**{**base, **over})


def _state(**over) -> HorizonState:
    base = {"epoch": ("long", 1), "opened_wall": _NOW - 600.0}
    return HorizonState(**{**base, **over})


def _facts(**over) -> HorizonFacts:
    base = {"seq": 1, "side": "long", "entry_px": 100.0, "mark_px": 100.05,
            "leverage": 3.0, "now_wall": _NOW, "px_age_s": 1.0, "cost_bps": 13.0}
    return HorizonFacts(**{**base, **over})


def _close() -> TradeDecision:
    return TradeDecision("close", None, 0.7, "exit")


def _snap(rule: HorizonRule, state: HorizonState, facts: HorizonFacts) -> dict:
    return build_snapshot(rule, state, facts)


# ---------------------------------------------------------------- H-1 / H-2 未装配与空转
def test_h1_unarmed_returns_same_object():
    """H-1：`rule is None` 时任意动作原样直通（**同一对象**，零副作用）。"""
    decision = _close()
    out, reason, _ = evaluate_horizon_gate(decision, {}, None, position_side="long")
    assert out is decision and reason is None


def test_h2_custom_all_defaults_equals_unarmed():
    """H-2：`custom` 全默认（0/1/0）⇒ 与未装配行为逐字段相等（「装配但空转」的安全退出口）。"""
    rule = HorizonRule(profile_id="custom")
    facts = _facts()
    snapshot = _snap(rule, _state(), facts)
    decision = _close()
    out, reason, _ = evaluate_horizon_gate(decision, snapshot, rule, position_side="long")
    assert out is decision and reason is None


# ---------------------------------------------------------------- H-3 ~ H-10 三道闸
def test_h3_min_hold_veto():
    """H-3：持仓不足最短窗口的 `close` 降级为 hold，reason 带前缀与两个可读数。"""
    rule, facts = _rule(), _facts()
    snapshot = _snap(rule, _state(opened_wall=_NOW - 600.0, exit_confirm_streak=2), facts)
    out, reason, _ = evaluate_horizon_gate(_close(), snapshot, rule, position_side="long")
    assert out.action == "hold"
    assert reason.startswith("horizon_min_hold(") and "min=120m" in reason


def test_h4_all_gates_satisfied_passes_through():
    """H-4：满足最短窗口 + 确认轮数 + 迟滞 ⇒ 原样直通（同一对象）。"""
    rule = _rule()
    facts = _facts(mark_px=100.5)          # +50bps > 1.5 × 13
    state = _state(opened_wall=_NOW - 20000.0, exit_confirm_streak=2)
    decision = _close()
    out, reason, _ = evaluate_horizon_gate(decision, _snap(rule, state, facts), rule,
                                        position_side="long")
    assert out is decision and reason is None


def test_h5_exit_confirm_streak_then_pass():
    """H-5：连续确认 1/2 被否；第二轮同意图放行。"""
    rule = _rule()
    facts = _facts(mark_px=100.5)
    first = _state(opened_wall=_NOW - 20000.0, exit_confirm_streak=1,
                   first_exit_intent_wall=_NOW)
    out, reason, _ = evaluate_horizon_gate(_close(), _snap(rule, first, facts), rule,
                                        position_side="long")
    assert out.action == "hold" and reason == "horizon_exit_unconfirmed(1/2)"
    second = _state(opened_wall=_NOW - 20000.0, exit_confirm_streak=2,
                    first_exit_intent_wall=_NOW)
    decision = _close()
    out2, reason2, _ = evaluate_horizon_gate(decision, _snap(rule, second, facts), rule,
                                          position_side="long")
    assert out2 is decision and reason2 is None


def test_h8_hysteresis_veto_on_small_profit():
    """H-8：浮盈 8.2bps < 1.5 × 13bps ⇒ 换手迟滞否决（获利了结推迟）。"""
    rule = _rule()
    facts = _facts(mark_px=100.082)
    state = _state(opened_wall=_NOW - 20000.0, exit_confirm_streak=2)
    out, reason, _ = evaluate_horizon_gate(_close(), _snap(rule, state, facts), rule,
                                        position_side="long")
    assert out.action == "hold" and reason.startswith("horizon_turnover_hysteresis(")


def test_h9_loss_exit_never_blocked_by_hysteresis():
    """H-9：浮亏离场**放行**——迟滞只约束获利了结（D-4；对亏损仓位施加迟滞等于延迟止损）。"""
    rule = _rule()
    facts = _facts(mark_px=99.7)           # −30bps
    state = _state(opened_wall=_NOW - 20000.0, exit_confirm_streak=2)
    decision = _close()
    out, reason, _ = evaluate_horizon_gate(decision, _snap(rule, state, facts), rule,
                                        position_side="long")
    assert out is decision and reason is None


def test_h10_cost_unavailable_is_fail_open():
    """H-10：`cost_bps is None` ⇒ 迟滞闸放行（fail-open，绝不因算不出而锁死持仓）。"""
    rule = _rule()
    facts = _facts(mark_px=100.02, cost_bps=None)
    state = _state(opened_wall=_NOW - 20000.0, exit_confirm_streak=2)
    decision = _close()
    out, reason, _ = evaluate_horizon_gate(decision, _snap(rule, state, facts), rule,
                                        position_side="long")
    assert out is decision and reason is None


# ---------------------------------------------------------------- H-11 ~ H-14 逐闸解除矩阵
def test_h11_loss_release_frees_all_three_gates():
    """H-11：浮亏达解除阈 ⇒ 三闸全解、`releaseReason=loss_release`。"""
    rule = _rule()
    # 止损 5% × 杠杆 3 × frac 0.5 = 7.5% ⇒ 价格 −2.6% 时保证金收益率 −7.8%
    facts = _facts(mark_px=97.4, stop_loss_pct=0.05)
    snapshot = _snap(rule, _state(), facts)
    assert snapshot["released"] and snapshot["releaseReason"] == "loss_release"
    assert snapshot["releasedGates"] == ["G1", "G2", "G3"]
    decision = _close()
    out, reason, _ = evaluate_horizon_gate(decision, snapshot, rule, position_side="long")
    assert out is decision and reason is None


@pytest.mark.parametrize("override", [
    {"stale_feed": True}, {"px_age_s": 999.0}, {"mark_px": 0.0}, {"entry_px": -1.0},
])
def test_h12_stale_px_release(override):
    """H-12：判定价不可信的四条路径一律全解（「算不出」绝不能变成「不许平」）。"""
    snapshot = _snap(_rule(), _state(), _facts(**override))
    assert snapshot["releaseReason"] == "stale_px"
    assert snapshot["releasedGates"] == ["G1", "G2", "G3"]


def test_h13_epoch_estimated_releases_only_g1():
    """H-13（[P0-2]）：估算 epoch **只解 G1**——同一断面下 G2 未满足仍被否。"""
    rule = _rule()
    facts = _facts(mark_px=100.5)
    state = _state(opened_wall=_NOW - 60.0, opened_wall_estimated=True,
                   exit_confirm_streak=1, first_exit_intent_wall=_NOW)
    snapshot = _snap(rule, state, facts)
    assert snapshot["releaseReason"] == "epoch_estimated"
    assert snapshot["releasedGates"] == ["G1"]
    out, reason, _ = evaluate_horizon_gate(_close(), snapshot, rule, position_side="long")
    assert out.action == "hold" and reason == "horizon_exit_unconfirmed(1/2)"


def test_h14_max_hold_bars_release():
    """H-14：到达强制时间平仓上限 ⇒ 三闸全解（此刻拦着只会让 Agent 比系统晚一步平，纯负收益）。"""
    snapshot = _snap(_rule(), _state(), _facts(max_hold_bars=4, bars_in_position=4))
    assert snapshot["releaseReason"] == "max_hold_bars"
    assert snapshot["releasedGates"] == ["G1", "G2", "G3"]


# ---------------------------------------------------------------- H-15 ~ H-17 豁免矩阵（冻结）
@pytest.mark.parametrize("action,side", [
    ("open_long", "flat"), ("open_short", "flat"), ("hold", "long"), ("cancel_order", "long"),
])
def test_h15_never_gated_actions(action, side):
    """H-15：空仓开仓 / hold / cancel_order **永不**被拦（豁免矩阵冻结）。"""
    assert gated_kind(action, side, gate_reverse=True) is None
    decision = TradeDecision(action, 0.5, 0.8, "r")
    out, reason, _ = evaluate_horizon_gate(decision, _snap(_rule(), _state(), _facts()),
                                        _rule(), position_side=side)
    assert out is decision and reason is None


def test_h16_reverse_is_gated_and_degrades_to_hold():
    """H-16：反手受约束；被否时整轮降级 hold（**绝不产生半反手态**）。"""
    rule = _rule()
    snapshot = _snap(rule, _state(opened_wall=_NOW - 60.0), _facts())
    decision = TradeDecision("open_short", 0.5, 0.8, "flip")
    out, reason, _ = evaluate_horizon_gate(decision, snapshot, rule, position_side="long")
    assert out.action == "hold" and reason.startswith("horizon_min_hold(")


def test_h17_gate_reverse_off_lets_reverse_through():
    """H-17：`gate_reverse=False` ⇒ 反手恒直通（退出口）。"""
    rule = _rule(gate_reverse=False)
    decision = TradeDecision("open_short", 0.5, 0.8, "flip")
    out, reason, _ = evaluate_horizon_gate(decision, _snap(rule, _state(), _facts()), rule,
                                        position_side="long")
    assert out is decision and reason is None


# ---------------------------------------------------------------- H-18 ~ H-21 解析与派生
def test_h18_resolve_four_layer_semantics():
    """H-18：总闸关 / 显式关 / 字段缺省 / 逐项覆盖 —— 四层语义各一条。"""
    off = _settings(ai_trade_horizon_enabled=False)
    assert resolve_horizon_rule(None, off, bar="1H") is None
    on = _settings()
    assert resolve_horizon_rule({"enabled": False}, on, bar="1H") is None
    default = resolve_horizon_rule(None, on, bar="1H")
    assert default is not None and default.profile_id == on.ai_trade_horizon_default_profile
    override = resolve_horizon_rule({"profile": "trend_hold", "minHoldSeconds": 60.0},
                                    on, bar="4H")
    assert override.profile_id == "trend_hold" and override.min_hold_s == 60.0


@pytest.mark.parametrize("bar,expected", [("1H", 12), ("4H", 3), ("5m", 144)])
def test_h19_expected_hold_bars_follows_bar(bar, expected):
    """H-19：`expected_hold_bars` 按**用户所选 bar** 现算（`intraday_swing` 的 43200s）。"""
    rule = resolve_horizon_rule({"profile": "intraday_swing"}, _settings(), bar=bar)
    assert rule.expected_hold_bars == expected


@pytest.mark.parametrize("key", ["hourly_swing", "intraday_swing", "short_swing", "trend_hold"])
def test_h20_min_hold_is_half_of_target_floor(key):
    """H-20：`min_hold_s == 0.5 × target_hold_min_s`（防手抄漂移；四个预设逐条断言）。"""
    profile = PROFILES[key]
    rule = resolve_horizon_rule({"profile": key}, _settings(), bar=profile.bar)
    assert rule.min_hold_s == pytest.approx(0.5 * profile.target_hold_min_s)
    assert rule.min_hold_s == pytest.approx(derive_min_hold_s(profile.target_hold_min_s))


def test_h21_profiles_frozen_shape():
    """H-21：预设表形状冻结；`custom` 条目除三个字符串外**全部为 None**（[P1-8]）。"""
    wire = profiles_wire(_settings())
    ids = [p["id"] for p in wire["profiles"]]
    assert ids == ["hourly_swing", "intraday_swing", "short_swing", "trend_hold", "custom"]
    custom = profile_wire(PROFILES["custom"])
    for key, value in custom.items():
        if key in ("id", "title", "description"):
            assert isinstance(value, str) and value
        else:
            assert value is None, f"custom.{key} 必须为 None（[P1-8]）"


# ---------------------------------------------------------------- H-22 ~ H-26 评审新增
def test_h22_confirm_window_elapsed_releases_g2():
    """H-22（[P0-3]）：确认窗口到期即视为确认完成——给真实持仓上的锁必须有钥匙。"""
    rule = _rule(exit_confirm_rounds=3)
    facts = _facts(mark_px=100.5, now_wall=_NOW + 4000.0)
    state = _state(opened_wall=_NOW - 20000.0, exit_confirm_streak=2,
                   first_exit_intent_wall=_NOW)
    snapshot = _snap(rule, state, facts)
    assert snapshot["confirmWindowElapsed"] is True
    decision = _close()
    out, reason, _ = evaluate_horizon_gate(decision, snapshot, rule, position_side="long")
    assert out is decision and reason is None
    # streak 归零时窗口起点一并清空 ⇒ 下一次意图重新起算（否则窗口在下一次意图时已经「到期」）。
    cleared = _snap(rule, _state(exit_confirm_streak=0, first_exit_intent_wall=None), facts)
    assert cleared["confirmWindowElapsed"] is False
    assert cleared["confirmWindowRemainingSeconds"] is None


@pytest.mark.parametrize("key", ["hourly_swing", "intraday_swing", "short_swing", "trend_hold"])
@pytest.mark.parametrize("bar", ["5m", "1H", "4H"])
def test_h23_confirm_window_formula(key, bar):
    """H-23（[P0-3]）：`confirm_window_s == min(0.25×target_min, 4×bar)`；下界 ≤0 ⇒ 0。"""
    profile = PROFILES[key]
    rule = resolve_horizon_rule({"profile": key}, _settings(), bar=bar)
    assert rule.confirm_window_s == pytest.approx(
        derive_confirm_window_s(profile.target_hold_min_s, bar))
    assert derive_confirm_window_s(0, bar) == 0.0


def test_confirm_window_falls_back_to_four_bars_when_no_target_lower_bound():
    """[P0-3] 的钥匙不能因「只填轮数、不填目标区间」而丢失（自定义模式的可达组合）。

    派生式把「目标下界未设」读成 0，其前提是「此时确认轮数也是 1、G2 本就空转」。用户在自定义里只填
    确认轮数时该前提不成立 ⇒ 会得到一道**没有墙钟上界的锁**（唤醒 0 的会话可无限期悬置一次离场意图）。
    故 `min(a, b)` 在 a 未定义时退化为 b = 四根执行周期，而不是把窗口机制整个关掉。
    """
    rule = resolve_horizon_rule({"profile": "custom", "exitConfirmRounds": 3},
                                _settings(), bar="4H")
    assert rule.exit_confirm_rounds == 3
    assert rule.confirm_window_s == pytest.approx(4 * 14400.0)
    # 全默认（轮数=1、G2 结构性空转）仍逐字回退 0 ⇒ 「装配但空转」的安全退出口不受影响。
    assert resolve_horizon_rule({"profile": "custom"}, _settings(), bar="4H").confirm_window_s == 0.0


def test_h24_custom_expected_hold_bars_is_none_not_one():
    """H-24（[P1-1]）：`custom` 全默认 ⇒ `expected_hold_bars is None`（**不是 1**），成本逐字回退。"""
    rule = resolve_horizon_rule({"profile": "custom"}, _settings(), bar="1H")
    assert rule.expected_hold_bars is None
    assert derive_expected_hold_bars(0, "1H") is None
    settings = _settings()
    assert (resolve_cost_params(settings, expected_hold_bars=None)
            == resolve_cost_params(settings))


def test_h26_blocked_streak_note_key_presence():
    """H-26（[P1-6]）：达阈才有 `blockedStreakNote` 键；未达 / 关闭 ⇒ **整键缺席**。"""
    rule = _rule(note_after_rounds=3)
    facts = _facts()
    assert "blockedStreakNote" not in _snap(rule, _state(blocked_streak=2), facts)
    assert _snap(rule, _state(blocked_streak=3), facts)["blockedStreakNote"] is True
    off = _rule(note_after_rounds=0)
    assert "blockedStreakNote" not in _snap(off, _state(blocked_streak=9), facts)


def test_gate_status_and_rule_wire_shape():
    """轨迹段状态四态 + 回执必须带两个派生量（[P1-9]/[P0-3]：只回显入参等于把派生关系藏起来）。"""
    rule = _rule()
    flat = _snap(rule, HorizonState(), _facts(side="flat"))
    assert gate_status(flat, None) == "idle"
    holding = _snap(rule, _state(), _facts())
    assert gate_status(holding, None) == "pass"
    assert gate_status(holding, "horizon_min_hold(...)") == "veto"
    released = _snap(rule, _state(), _facts(stale_feed=True))
    assert gate_status(released, None) == "released"
    wire = rule_wire(rule)
    assert wire["expectedHoldBars"] == 12 and wire["confirmWindowSeconds"] == 3600.0
    assert rule_wire(None) is None
