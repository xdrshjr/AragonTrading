"""档位地板判据（ai-trade-fill-truth-and-effective-controls 轴 C-1）+ 档位分辨率（轴 C-2）。

门禁 `G-C1`~`G-C7`。其中 `G-C7` **在合入前必须是红的**：今天 `f_raw == 0` 的那一轮会撞进
`_do_open` 的 dust 分支，`dust_skips += 1` 并对用户说「预算 / 杠杆过小」——而风控解出零跟预算
大小毫无关系，照那条提示去加预算只会让下一次开得更大。
"""
from __future__ import annotations

import pytest

from app.ai_auto.decider import TradeDecision
from app.ai_auto.gear_resolution import (
    describe_gear_resolution,
    gear_resolution_note,
)
from app.ai_auto.sizing import (
    SizingFloorPolicy,
    SizingInputs,
    SizingRule,
    parse_floor_policy,
    resolve_size_fraction,
)
from app.ai_auto.sizing_runtime import SizingContext, SizingRuntime
from app.config import Settings

_RULE = SizingRule(vol_target_annual=0.2, vol_window=20, kelly_lambda=0.25, f_max=1.0,
                   exposure_cap_mult=1.0, corr_min_samples=5)
_BAR_SECONDS = 900


def _inputs(**over) -> SizingInputs:
    base = {"action": "open_long", "model_fraction": 0.5, "p_win": None, "cost_bps": None,
            "stop_bps": None, "take_bps": None, "realized_vol_bar": 0.01,
            "brake_mult": 1.0, "corr_scale": 1.0, "step": 0.5}
    base.update(over)
    return SizingInputs(**base)


def _solve(policy=None, **over):
    return resolve_size_fraction(_inputs(**over), _RULE, _BAR_SECONDS, policy)


# ---------- G-C1：解出零 ⇒ 不开这一单 ----------

def test_a_risk_budget_of_exactly_zero_is_a_veto_not_a_minimum_gear():
    """`corr_scale = 0` ⇒ `f_raw = 0` ⇒ `fraction == 0.0` / `floor_verdict == "solved_zero"`。

    这不是一个阈值，是一个**恒等式**：`corr_scale ∈ [0,1]`、`brake_mult ∈ (0,1]`，解出精确的 0
    只有一个含义——这一单的风险预算是零。生产实测 DOGE 有两轮 `avgScaleRatio = 0.0`，而
    `quantize_size_fraction` 的 `max(1, …)` 把它抬成了半仓（那两轮恰好限价单没成交，是运气不是设计）。
    """
    out = _solve(SizingFloorPolicy(zero_is_veto=True), corr_scale=0.0)
    assert out.fraction == 0.0 and out.floor_verdict == "solved_zero"
    assert out.wire["floorVerdict"] == "solved_zero"


# ---------- G-C2：闸关 ⇒ 逐字回退 ----------

def test_legacy_floor_switch_cannot_lift_a_zero_risk_budget():
    """旧地板开关不允许突破已装配仓位层的硬上限。"""
    assert _solve(None, corr_scale=0.0).fraction == 0.0
    assert _solve(SizingFloorPolicy(zero_is_veto=False), corr_scale=0.0).fraction == 0.0


# ---------- G-C3：floor_ratio 默认 0 ⇒ 第二条判据不存在 ----------

def test_floor_ratio_zero_cannot_lift_a_positive_risk_budget():
    policy = SizingFloorPolicy(zero_is_veto=True, floor_ratio=0.0)
    for corr in (0.05, 0.2, 0.37, 0.9):
        out = _solve(policy, corr_scale=corr)
        assert out.fraction == 0.0, corr
        assert out.floor_verdict == "below_gear_floor", corr


def test_floor_ratio_when_armed_vetoes_a_positive_but_tiny_solution():
    policy = SizingFloorPolicy(zero_is_veto=True, floor_ratio=0.5)
    out = _solve(policy, corr_scale=0.2)          # f_raw = 0.5 × 0.2 = 0.10 < 0.5 × 0.5
    assert out.fraction == 0.0 and out.floor_verdict == "below_gear_floor"


def test_default_settings_arm_only_the_zero_criterion():
    """默认世界：`zero_is_veto` 开、`floor_ratio` 关（后者杀伤面大且缺生产依据）。"""
    policy = parse_floor_policy(Settings())
    assert policy.zero_is_veto is True and policy.floor_ratio == 0.0


def test_continuous_ladder_skips_the_floor_criterion():
    """`step <= 0`（连续档位）⇒ 没有「地板」这个概念，第二条判据整条跳过。"""
    policy = SizingFloorPolicy(zero_is_veto=True, floor_ratio=0.9)
    assert _solve(policy, step=0.0, corr_scale=0.2).floor_verdict == "none"


# ---------- G-C7（RV-P1-4，合入前必须红）：改成 hold，绝不撞 dust 分支 ----------

class _FakeExecutor:
    """只承载 `dust_skips` 与 `risk` 两个属性的替身（本用例不需要真的下单）。"""

    def __init__(self):
        self.dust_skips = 0
        self.risk = None
        self.budget_usd = 1000.0
        self.leverage = 3.0


def test_a_zeroed_solution_becomes_hold_and_never_touches_dust_skips():
    """`f_raw == 0` 的一轮：决策必须在 `_execute_decision` **之前**变成 hold。

    只把 `size_fraction` 置 0 是不够的——`apply_size_fraction` 刻意「只改 size_fraction、action
    一字不动」，于是这个 `open_*` 会继续往下走撞进 `_do_open` 的 `sz <= 0` 分支，后果有两个都
    不能接受：①同一件事被记两次（`sizing_zeroed` 与 `dust_skips` 各 +1）；②产品对用户说了一句
    假话——那条分支推的是「预算 / 杠杆过小」，而纪律自检会据 `dust_skips` 写「提高总预算可消除」。
    """
    runtime = SizingRuntime(_RULE, None, None, inst_id="DOGE-USDT-SWAP",
                            bar_seconds=_BAR_SECONDS,
                            floor_policy=SizingFloorPolicy(zero_is_veto=True))
    runtime._vol = 0.01
    executor = _FakeExecutor()
    decision = TradeDecision(action="open_long", size_fraction=0.5, confidence=0.8,
                             reason="模型想开")
    ctx = SizingContext(ref_px=0.08, atr_px=None, step=0.5, now_wall=0.0,
                        p_win=None, cost_bps=None)
    # 把相关性缩减解成 0，走**完整的 `resolve()` 路径**（组合有效敞口满档时 `corr_scale` 就是 0，
    # 生产实测 DOGE 那两轮正是这个断面）。替身只替一个取数点，判定与装配全部走真代码。
    runtime._correlation = lambda action, pre, c, ex: (None, 0.0)

    final, _plan, wire = runtime.resolve(decision, ctx, executor)

    assert final.action == "hold"                     # ★ 在 `_execute_decision` 之前就停住
    assert final.size_fraction is None
    assert "预算" not in (final.reason or "")          # 绝不对用户说一个错误的原因
    assert executor.dust_skips == 0                   # ★ 一次都不许碰它（G-C7 的核心断言）
    assert runtime.counters.solved_zero_vetoes == 1
    assert wire["floorVerdict"] == "solved_zero"

    # 旧开关关闭仍保留风险上限，拒绝不能映射为零强度开仓。
    off = SizingRuntime(_RULE, None, None, inst_id="DOGE-USDT-SWAP",
                        bar_seconds=_BAR_SECONDS, floor_policy=None)
    off._vol = 0.01
    off._correlation = lambda action, pre, c, ex: (None, 0.0)
    final_off, _, _ = off.resolve(decision, ctx, _FakeExecutor())
    assert final_off.action == "hold" and final_off.size_fraction is None


def test_floor_verdict_maps_to_hold_with_a_reason_that_is_not_about_budget():
    """否决产物与其余各闸逐字同形（`action == "hold"` / `size_fraction is None`），

    且 `reason` **不含「预算」二字**——风控解出零跟预算大小毫无关系。
    """
    from app.ai_auto.sizing_runtime import _to_hold

    hold = _to_hold(TradeDecision(action="open_long", size_fraction=0.5,
                                  confidence=0.8, reason="模型想开"), "solved_zero")
    assert hold.action == "hold" and hold.size_fraction is None
    assert hold.reason == "sizing_solved_zero"
    assert "预算" not in hold.reason


# ---------- 轴 C-2：档位分辨率 ----------

def test_gear_resolution_flags_the_coarse_default_world_and_clears_a_fine_one():
    """`G-C4`：`step=0.5` 三个机制全 erased；`step=0.05` 空集；`step<=0` 空集。"""
    coarse = describe_gear_resolution(0.5)
    assert set(coarse.erased) == {"contrarian"}
    assert coarse.gears == 2 and coarse.min_gear == 0.5
    assert describe_gear_resolution(0.05).erased == ()
    assert describe_gear_resolution(0.25).erased == ()
    assert describe_gear_resolution(0.0).erased == ()
    # `step=1.0`（每次全仓）：阶梯只有一格，任何缩减都不可能发生。
    assert set(describe_gear_resolution(1.0).erased) == {"contrarian"}


def test_gear_note_is_absent_when_nothing_is_erased():
    assert gear_resolution_note(describe_gear_resolution(0.05)) is None
    note = gear_resolution_note(describe_gear_resolution(0.5))
    assert note is not None and "逆势通路强制降档" in note
    assert "回撤刹车第一档" not in note and "相关性缩减" not in note


def test_gear_note_never_contains_literal_bold_markers():
    """`G-X3`：后端文案在 JSX 里是纯文本节点，`**粗体**` 会原样显示成星号（第 5 次登记）。"""
    for step in (0.5, 0.25, 0.1, 0.05, 1.0):
        note = gear_resolution_note(describe_gear_resolution(step))
        assert note is None or "**" not in note


@pytest.mark.parametrize("step", [0.5, 0.25, 0.1, 0.05, 1.0, 0.0])
def test_describe_gear_resolution_never_raises(step):
    res = describe_gear_resolution(step, brake_rule=object(), consensus_rule=None)
    assert isinstance(res.erased, tuple) and isinstance(res.survives, tuple)
    assert set(res.erased) | set(res.survives) == {"brakeSlow", "contrarian", "corrScale"}


@pytest.mark.parametrize("step", [0.5, 1.0])
@pytest.mark.parametrize("key,inputs", [
    ("brakeSlow", {"brake_mult": 0.5}),
    ("corrScale", {"corr_scale": 0.5}),
])
def test_gear_diagnosis_agrees_with_enforced_risk_reduction(step, key, inputs):
    """已被仓位层拒绝的最小档，不应再向用户宣称风险缩减失效。"""
    outcome = _solve(None, model_fraction=step, step=step, **inputs)
    assert outcome.fraction == 0.0
    resolution = describe_gear_resolution(step)
    assert resolution.dead_gears[key] == 0
    assert key not in resolution.erased


@pytest.mark.parametrize("action", ["open_long", "open_short"])
@pytest.mark.parametrize("step,cap,expected", [
    (0.5, 200.0, None), (0.25, 200.0, 0.25),
    (0.05, 218.0, 0.4), (0.0, 218.0002, 218.0002 / 500),
    (0.25, 250.0, 0.5), (0.25, 249.999999, 0.25),
])
def test_final_risk_fraction_never_rounds_above_exposure_cap(action, step, cap, expected):
    """验证最终动作和名义额，覆盖原始三类超限及展示舍入反例。"""
    from app.ai_auto.exposure_book import GroupExposureBook
    from app.ai_auto.signals import decision_to_signals

    book = GroupExposureBook(unit_notional=cap, cap_mult=1.0, bar_seconds=900)
    runtime = SizingRuntime(_RULE, inst_id="A", book=book, bar_seconds=900)
    decision = TradeDecision(action=action, size_fraction=0.5, confidence=0.8, reason="测试")
    final, _, _ = runtime.resolve(decision, SizingContext(step=step, notional_full=500.0))
    if expected is None:
        assert final.action == "hold" and decision_to_signals(final) == []
        assert runtime.counters.below_floor_vetoes == 1
    else:
        assert final.action == action
        assert final.size_fraction == pytest.approx(expected, abs=1e-14)
        assert final.size_fraction * 500 <= cap + 1e-12


@pytest.mark.parametrize("raw", [0.0, 1e-14, 0.249999999, float("nan"), float("inf")])
@pytest.mark.parametrize("policy", [None, SizingFloorPolicy(False, 0.0)])
def test_no_legal_gear_never_becomes_an_open_signal(raw, policy):
    from app.ai_auto.signals import decision_to_signals

    runtime = SizingRuntime(_RULE, floor_policy=policy)
    decision = TradeDecision(action="open_long", size_fraction=raw, confidence=0.8, reason="测试")
    final, _, _ = runtime.resolve(decision, SizingContext(step=0.25))
    assert final.action == "hold"
    assert decision_to_signals(final) == []


@pytest.mark.parametrize("step,proposal,expected", [
    (0.05, 0.45, 0.45), (0.1, 0.45, 0.4), (0.25, 0.45, None),
])
def test_rounded_hedge_must_remain_inside_feasible_interval(step, proposal, expected):
    from app.ai_auto.exposure_book import GroupExposureBook

    book = GroupExposureBook(unit_notional=200, cap_mult=1, bar_seconds=900)
    book.publish_notional("B", 400)
    runtime = SizingRuntime(_RULE, inst_id="A", book=book)
    decision = TradeDecision(action="open_short", size_fraction=proposal, confidence=0.8, reason="对冲")
    final, _, _ = runtime.resolve(decision, SizingContext(step=step, notional_full=500))
    assert final.size_fraction == expected
    assert final.action == ("hold" if expected is None else "open_short")


@pytest.mark.parametrize("floor", ["below_gear_floor", "exposure_cap", "invalid_quantity", None])
def test_risk_veto_has_an_explainable_diagnosis(floor):
    from types import SimpleNamespace
    from app.ai_auto.integrity_view import _diagnosis

    session = SimpleNamespace(_stale_degraded=False,
        _last_decision={"sizingVerdict": {"floorVerdict": floor},
            "executed": {"executionReceipt": {
                "status": "not_submitted", "reason": "sizing_exposure_cap"}}},
        executor=SimpleNamespace(resting=None, projected="flat"))
    diagnosis = _diagnosis(session, None)
    assert "风险" in diagnosis["message"]
    assert "预算" not in diagnosis["message"]
