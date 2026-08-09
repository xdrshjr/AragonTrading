"""开仓侧换手纪律纯核心单测（ai-trade-turnover-budget-and-backstop §6.1 · T 组）。

覆盖四模式派生值逐格相等 / custom 结构性关闭 / refill 幂等与时间倒流保护 / 令牌非负 /
[P0-1] 三分支（flat⇒hold、反手⇒close、同向加仓⇒直通、未知立场⇒放行）/
[P0-4] 冷却方向不取自成交字典 / G4 先于 G5 / 降级码格式 / 长期速率上界 / 零样本口径。
纯函数，无 IO、无 LLM、无 broker。
"""
from __future__ import annotations

import ast
import io
import pathlib
import re
import tokenize

import pytest

from app.ai_auto.decider import TradeDecision
from app.ai_auto.hold_horizon import PROFILES, resolve_horizon_rule
from app.ai_auto.horizon_runtime import HorizonRuntime
from app.ai_auto.turnover_discipline import (
    REASON_BUDGET,
    REASON_REENTRY,
    SKIP_UNKNOWN_SIDE,
    TurnoverRule,
    apply_turnover_gate,
    build_wire,
    derive_reentry_cooldown_s,
    derive_turnover_capacity,
    derive_turnover_per_day,
    gate_skip_reason,
    init_state,
    on_close_fill,
    on_open_fill,
    refill,
    rule_from_horizon,
)
from app.config import Settings

_NOW = 1_700_000_000.0

# §2.2.1 的落地值表：实现后**必须与本表逐格相等**（T-1）。
_EXPECTED = {
    #  profile          min_hold  cooldown  per_day  capacity
    "hourly_swing":     (180.0,     90.0,     24.0,    12.0),
    "intraday_swing":   (7200.0,    3600.0,    6.0,     3.0),
    "short_swing":      (21600.0,   10800.0,   2.0,     2.0),
    "trend_hold":       (43200.0,   21600.0,   1.0,     2.0),
}


def _settings() -> Settings:
    return Settings()


def _rule(**over) -> TurnoverRule:
    base = {"reentry_cooldown_s": 3600.0, "reentry_scope": "same_side",
            "per_day": 6.0, "capacity": 3.0}
    return TurnoverRule(**{**base, **over})


def _wire(state, rule, *, now_wall=_NOW) -> dict:
    return build_wire(state, rule, now_wall=now_wall)


def _open(action="open_long") -> TradeDecision:
    return TradeDecision(action, 0.5, 0.7, "signal")


# ---------------- T-1 / T-19：派生式 ----------------
@pytest.mark.parametrize("profile_id", sorted(_EXPECTED))
def test_profile_derived_values_match_the_spec_table(profile_id):
    """T-1：四个预设的三条派生值与 §2.2.1 表**逐格相等**（含 hourly_swing 被硬上限夹到 24）。"""
    profile = PROFILES[profile_id]
    rule = resolve_horizon_rule({"profile": profile_id}, _settings(), bar=profile.bar)
    assert (rule.min_hold_s, rule.reentry_cooldown_s, rule.turnover_per_day,
            rule.turnover_capacity) == _EXPECTED[profile_id]


def test_derive_turnover_per_day_returns_both_capped_and_raw():
    """T-19 [P1-4]：一次调用同时给出已夹与未夹两个值——弱门槛自认句只能据未夹的那个产出。"""
    assert derive_turnover_per_day(360.0) == (24.0, 240.0)
    capped, raw = derive_turnover_per_day(360.0)
    assert capped != raw                       # hourly_swing：硬上限确实咬住了
    assert derive_turnover_per_day(14400.0) == (6.0, 6.0)
    assert derive_turnover_per_day(0.0) == (None, None)
    assert derive_turnover_per_day(None) == (None, None)


def test_acceptance_thresholds_and_runtime_budget_are_the_same_number():
    """S-8 / 放行条件 3：弹窗承诺的门槛、运行期执行的预算、事后验收的判据恒为**同一个数**。"""
    from app.ai_auto.horizon_acceptance import resolve_thresholds

    settings = _settings()
    for profile_id, profile in PROFILES.items():
        rule = resolve_horizon_rule({"profile": profile_id}, settings,
                                    bar=profile.bar or "1H")
        assert resolve_thresholds(settings, rule).max_turnover_per_day == \
            rule.turnover_per_day, profile_id


def test_no_second_place_computes_seconds_per_day_over_lower_bound():
    """放行条件 3：`86400 / 目标下界` 在**代码**里只有一处（`turnover_discipline`）。

    判据必须先剥掉注释与字符串再扫：本仓库刚吃过「机械判据自己算错就等于判据失效」的亏，而
    正文里解释这条纪律的那句话本身就含 `86400 / lo`——按裸文本扫会把一句说明当成一次违规。
    """
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "ai_auto"
    pattern = re.compile(r"(86400(\.0)?|_SECONDS_PER_DAY)\s*/\s*(lo\b|float\(lo)")
    hits = sorted(p.name for p in root.glob("*.py")
                  if pattern.search(_code_only(p.read_text(encoding="utf-8"))))
    assert hits == ["turnover_discipline.py"], f"派生式被抄了第二遍：{hits}"


def _code_only(source: str) -> str:
    """剥掉注释与字符串字面量，只留可执行代码（判据不该被文档措辞影响）。"""
    out: list[str] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(token.string)
    return " ".join(out)


def test_capacity_min_is_a_safety_valve_not_a_rounding_artifact():
    """D-2：容量下限 2 让 `trend_hold`（速率 1 次/日）不会在一次早期止损后冻结 22 小时。"""
    assert derive_turnover_capacity(1.0, burst_frac=0.5, min_capacity=2.0) == 2.0
    assert derive_turnover_capacity(24.0, burst_frac=0.5, min_capacity=2.0) == 12.0
    assert derive_turnover_capacity(None, burst_frac=0.5, min_capacity=2.0) == 0.0


def test_reentry_cooldown_derives_from_min_hold_not_target_lower_bound():
    """G4 与 G1 是同一件事的两半，故用同一个基准（`min_hold_s`）才能量纲一致。"""
    assert derive_reentry_cooldown_s(7200.0, frac=0.5) == 3600.0
    assert derive_reentry_cooldown_s(0.0, frac=0.5) == 0.0      # custom 全默认 ⇒ 结构性关闭
    assert derive_reentry_cooldown_s(None, frac=0.5) == 0.0


# ---------------- T-2：custom 全默认 = 逐字等价于未装配 ----------------
def test_custom_all_default_is_byte_equivalent_to_not_installed():
    """T-2：`rule_from_horizon` 返回 `None` ⇒ 三个函数全部零动作、`build_wire` 为 `None`。"""
    settings = _settings()
    rule = resolve_horizon_rule({"profile": "custom"}, settings, bar="1H")
    turnover = rule_from_horizon(rule, settings)
    assert turnover is None
    state = init_state(turnover, now_wall=_NOW)
    assert refill(state, turnover, now_wall=_NOW + 86400) == state
    assert on_open_fill(state, turnover, now_wall=_NOW) == state
    assert on_close_fill(state, turnover, closed_side="long", now_wall=_NOW) == state
    assert build_wire(state, turnover, now_wall=_NOW) is None


def test_master_switch_off_zeroes_the_contract_itself():
    """总闸关 ⇒ 契约的四个字段全部回到关闭值（宪章与 rule_wire 直接读它们，不能只在投影处拦）。"""
    settings = Settings(ai_trade_horizon_turnover_enabled=False)
    rule = resolve_horizon_rule({"profile": "intraday_swing"}, settings, bar="1H")
    assert rule.reentry_cooldown_s == 0.0 and rule.turnover_per_day is None
    assert rule_from_horizon(rule, settings) is None


# ---------------- T-3 / T-4 / T-18：状态推进 ----------------
def test_refill_is_idempotent_and_never_overflows_capacity():
    """T-3：同一 `now_wall` 调两次结果相同；跨 24h 后 `tokens == capacity`（不溢出）。"""
    rule = _rule()
    state = init_state(rule, now_wall=_NOW)
    state = on_open_fill(state, rule, now_wall=_NOW)             # 3 - 1 = 2
    once = refill(state, rule, now_wall=_NOW + 3600)
    assert refill(once, rule, now_wall=_NOW + 3600) == once
    assert refill(state, rule, now_wall=_NOW + 86400).tokens == pytest.approx(3.0)


def test_on_open_fill_floors_tokens_at_zero():
    """T-4：`tokens` 恒 `>= 0`——允许为负会额外多冻结一个补充周期，用户推不出这段等待从哪来。"""
    rule = _rule()
    state = init_state(rule, now_wall=_NOW)
    for _ in range(10):
        state = on_open_fill(state, rule, now_wall=_NOW)
    assert state.tokens == 0.0
    assert state.opens_counted == 10


def test_refill_protects_against_a_clock_going_backwards():
    """T-18 [P1-8]：回测 `note_open` 传的是**开仓根 ts**，epoch 迟采纳时它会早于上一次 refill。

    不夹 ⇒ 令牌不增反减且不报错；只夹 elapsed 却回写更早的 `last_refill_wall` ⇒ 同一段时间会被
    补第二遍。故两者都要守。
    """
    rule = _rule()
    state = on_open_fill(init_state(rule, now_wall=_NOW), rule, now_wall=_NOW + 7200)
    back = refill(state, rule, now_wall=_NOW)                    # 时间倒流
    assert back.tokens >= state.tokens                           # 绝不减少
    assert back.last_refill_wall == state.last_refill_wall       # 单调，不回退
    assert refill(back, rule, now_wall=_NOW + 7200) == back      # 不重复补同一段


# ---------------- T-5 / T-6 / T-8 / T-9 / T-10：判定 ----------------
def _cooled(side="long", **rule_over):
    rule = _rule(**rule_over)
    state = on_close_fill(init_state(rule, now_wall=_NOW), rule,
                          closed_side=side, now_wall=_NOW)
    return rule, state


def test_g4_blocks_only_the_side_that_was_just_closed():
    """T-5：`same_side` 只拦被平掉的那一侧——反向开仓是结构翻转的合法表达（D-3）。"""
    rule, state = _cooled("long")
    wire = _wire(state, rule)
    blocked, reason = apply_turnover_gate(_open("open_long"), wire, rule,
                                          position_side="flat")
    assert blocked.action == "hold" and reason.startswith(REASON_REENTRY)
    passed = _open("open_short")
    assert apply_turnover_gate(passed, wire, rule, position_side="flat") == (passed, None)


def test_g4_scope_both_blocks_both_directions():
    """T-6：`scope="both"` 两向都拦。"""
    rule, state = _cooled("long", reentry_scope="both")
    wire = _wire(state, rule)
    for action in ("open_long", "open_short"):
        gated, reason = apply_turnover_gate(_open(action), wire, rule,
                                            position_side="flat")
        assert gated.action == "hold" and reason.startswith(REASON_REENTRY)


@pytest.mark.parametrize("action", ["close", "hold", "cancel_order"])
@pytest.mark.parametrize("side", ["flat", "long", "short", "weird"])
def test_exits_are_never_blocked_in_any_state(action, side):
    """T-7 / 放行条件 6：`close` / `hold` / `cancel_order` 在**任何**状态下返回同一个对象。

    这是本模块的第一不变式：把平仓能力交给一个换手预算去管，等于让一个统计口径获得持仓生杀权。
    """
    rule, state = _cooled("long")
    state = state.__class__(**{**state.__dict__, "tokens": 0.0})   # 令牌也一并打空
    decision = TradeDecision(action, None, 0.6, "exit")
    out, reason = apply_turnover_gate(decision, _wire(state, rule), rule,
                                      position_side=side)
    assert out is decision and reason is None


def test_g4_is_evaluated_before_g5():
    """T-8：两者同时激活时出更具体的那一个（冷却是「刚刚发生了一件事」）。"""
    rule, state = _cooled("long")
    state = state.__class__(**{**state.__dict__, "tokens": 0.0})
    _, reason = apply_turnover_gate(_open("open_long"), _wire(state, rule), rule,
                                    position_side="flat")
    assert reason.startswith(REASON_REENTRY)


def test_budget_reason_carries_the_eta_of_the_next_token():
    """T-9：降级码必须带「下一枚何时到账」——只说被拦，用户无法判断该等还是该改配置。"""
    rule = _rule()
    state = init_state(rule, now_wall=_NOW)
    for _ in range(3):
        state = on_open_fill(state, rule, now_wall=_NOW)
    state = refill(state, rule, now_wall=_NOW + 6048)          # 6/日 ⇒ 0.42 枚
    _, reason = apply_turnover_gate(_open(), _wire(state, rule, now_wall=_NOW + 6048),
                                    rule, position_side="flat")
    assert re.fullmatch(r"horizon_turnover_budget\(0\.4/3,next=\d+m\)", reason), reason


def test_g5_is_structurally_off_when_per_day_is_none():
    """T-10：`per_day=None` ⇒ 本闸结构性空转（哪怕 `tokens=0`）。"""
    rule = _rule(per_day=None, capacity=0.0, reentry_cooldown_s=0.0)
    state = init_state(rule, now_wall=_NOW)
    decision = _open()
    assert apply_turnover_gate(decision, _wire(state, rule), rule,
                               position_side="flat") == (decision, None)


def test_long_run_rate_is_bounded_by_per_day_plus_capacity():
    """T-11：模拟 7 天、每次有令牌就开仓 ⇒ 总开仓数 ≤ `per_day × 7 + capacity`。"""
    rule = _rule(reentry_cooldown_s=0.0)
    state = init_state(rule, now_wall=_NOW)
    opened = 0
    for step in range(7 * 24 * 6):                       # 每 10 分钟试一次
        now = _NOW + step * 600.0
        state = refill(state, rule, now_wall=now)
        if state.tokens >= 1.0:
            state = on_open_fill(state, rule, now_wall=now)
            opened += 1
    assert opened <= rule.per_day * 7 + rule.capacity


def test_trend_hold_can_reopen_after_one_quick_stop_out():
    """T-12 / D-2：`min_capacity=2` 的安全阀——「开→立刻平→再开」不被 G5 拦（只被 G4 拦）。"""
    settings = _settings()
    rule = rule_from_horizon(
        resolve_horizon_rule({"profile": "trend_hold"}, settings, bar="4H"), settings)
    state = on_open_fill(init_state(rule, now_wall=_NOW), rule, now_wall=_NOW)
    state = on_close_fill(state, rule, closed_side="long", now_wall=_NOW + 7200)
    after_cooldown = _NOW + 7200 + rule.reentry_cooldown_s + 1
    state = refill(state, rule, now_wall=after_cooldown)
    decision = _open("open_long")
    assert apply_turnover_gate(decision, _wire(state, rule, now_wall=after_cooldown),
                               rule, position_side="flat") == (decision, None)


def test_wire_uses_null_not_zero_for_inactive_readings():
    """T-13：零样本口径——未激活时 `cooldownRemainingSeconds` 为 `null` 而非 `0`。"""
    rule = _rule()
    wire = _wire(init_state(rule, now_wall=_NOW), rule)
    assert wire["cooldownRemainingSeconds"] is None
    assert wire["cooldownSide"] is None
    assert wire["blocked"] is False and wire["nextTokenSeconds"] is None
    assert wire["budgetScope"] == "session"          # [P1-5] 口径必须自认到「会话」这一层


# ---------------- T-14 / T-15 / T-16：[P0-1] 三分支 ----------------
@pytest.mark.parametrize("held,action", [("short", "open_long"), ("long", "open_short")])
def test_a_blocked_reverse_downgrades_to_close_not_hold(held, action):
    """T-14 [P0-1]：反手含一条平仓腿——降级为 `hold` 会把持仓留在原地。

    降级为 `close` 同时守住两件事：**退出永远能执行**与**再入场受预算约束**。
    """
    rule = _rule(reentry_cooldown_s=0.0)
    state = init_state(rule, now_wall=_NOW)
    for _ in range(3):
        state = on_open_fill(state, rule, now_wall=_NOW)          # 令牌打空
    gated, reason = apply_turnover_gate(_open(action), _wire(state, rule), rule,
                                        position_side=held)
    assert gated.action == "close"
    assert "reverse→close" in reason


def test_same_side_scale_in_always_passes_through():
    """T-15 [P0-1] / D-12：加仓不建新 epoch、不消耗令牌，被预算拦住用户推不出原因。"""
    rule, state = _cooled("long")
    state = state.__class__(**{**state.__dict__, "tokens": 0.0})
    wire = _wire(state, rule)
    for held, action in (("long", "open_long"), ("short", "open_short")):
        decision = _open(action)
        assert apply_turnover_gate(decision, wire, rule,
                                   position_side=held) == (decision, None)


def test_only_a_flat_open_downgrades_to_hold_and_unknown_side_passes():
    """T-16 [P0-1]：`flat` ⇒ `hold`；立场落在值域外 ⇒ 恒放行并如实记 `unknown_side`。"""
    rule = _rule(reentry_cooldown_s=0.0)
    state = init_state(rule, now_wall=_NOW)
    for _ in range(3):
        state = on_open_fill(state, rule, now_wall=_NOW)
    gated, reason = apply_turnover_gate(_open(), _wire(state, rule), rule,
                                        position_side="flat")
    assert gated.action == "hold" and reason.startswith(REASON_BUDGET)
    decision = _open()
    assert apply_turnover_gate(decision, _wire(state, rule), rule,
                               position_side="weird") == (decision, None)
    assert gate_skip_reason("weird") == SKIP_UNKNOWN_SIDE
    assert gate_skip_reason("flat") is None


def test_runtime_records_gate_skipped_on_an_unknown_side():
    """立场未知时 fail-open，但 wire 里如实记号（绝不静默）。"""
    settings = _settings()
    horizon = resolve_horizon_rule({"profile": "intraday_swing"}, settings, bar="1H")
    runtime = HorizonRuntime(horizon,
                             turnover_rule=rule_from_horizon(horizon, settings))
    runtime.last_snapshot = {"turnover": {"cooldownActive": False}}
    decision = _open()
    out, degrade = runtime.turnover_gate(decision, None, position_side="weird",
                                         now_wall=_NOW)
    assert out is decision and degrade is None
    assert runtime.last_snapshot["turnover"]["gateSkipped"] == SKIP_UNKNOWN_SIDE


# ---------------- T-17：[P0-4] 冷却方向的真源 ----------------
@pytest.mark.parametrize("trade", [{"side": "close"}, {}])
def test_cooldown_side_comes_from_the_epoch_not_the_fill_dict(trade):
    """T-17 [P0-4]：实盘 close 成交的 `side` 恒为 `"close"`、回测干脆传 `{}`。

    照成交字典取方向 ⇒ `cooldown_side == "close"` ⇒ 与 `open_long`/`open_short` 永远不匹配 ⇒
    **永远不拦、永远不报错**。真源必须是 `state.epoch[0]`，且要在 `_consume_epoch_hold()` 之前取。
    """
    settings = _settings()
    horizon = resolve_horizon_rule({"profile": "intraday_swing"}, settings, bar="1H")
    runtime = HorizonRuntime(horizon,
                             turnover_rule=rule_from_horizon(horizon, settings))
    runtime.note_open({"side": "long"}, _NOW, seq=1)
    runtime.note_close(trade, _NOW + 7200)
    wire = build_wire(runtime.turnover_state, runtime.turnover_rule,
                      now_wall=_NOW + 7201)
    assert wire["cooldownActive"] is True
    assert wire["cooldownSide"] == "long"


def test_no_epoch_means_no_cooldown_at_all():
    """T-17 后半：`epoch is None` ⇒ **不开启冷却**（宁可不拦，也不拿一个猜的方向去拦）。"""
    settings = _settings()
    horizon = resolve_horizon_rule({"profile": "intraday_swing"}, settings, bar="1H")
    runtime = HorizonRuntime(horizon,
                             turnover_rule=rule_from_horizon(horizon, settings))
    runtime.note_close({"side": "close"}, _NOW)
    wire = build_wire(runtime.turnover_state, runtime.turnover_rule, now_wall=_NOW)
    assert wire["cooldownActive"] is False


# ---------------- S-18：依赖方向 ----------------
def test_module_never_imports_hold_horizon():
    """S-18 [P1-9]：依赖方向恒为 `hold_horizon → turnover_discipline`，反向即成环。"""
    src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "ai_auto"
           / "turnover_discipline.py").read_text(encoding="utf-8")
    imported = {node.module for node in ast.walk(ast.parse(src))
                if isinstance(node, ast.ImportFrom) and node.level > 0}
    assert "hold_horizon" not in imported
    assert imported <= {"acceptance_text", "decider"}
