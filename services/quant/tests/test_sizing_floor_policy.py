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

def test_gate_off_still_lifts_zero_to_the_minimum_gear():
    """总闸 8 关 ⇒ `f_raw == 0` 仍被抬到最小一格（今天的行为，逐字回退）。"""
    assert _solve(None, corr_scale=0.0).fraction == 0.5
    assert _solve(SizingFloorPolicy(zero_is_veto=False), corr_scale=0.0).fraction == 0.5


# ---------- G-C3：floor_ratio 默认 0 ⇒ 第二条判据不存在 ----------

def test_floor_ratio_zero_makes_the_second_criterion_unreachable():
    policy = SizingFloorPolicy(zero_is_veto=True, floor_ratio=0.0)
    for corr in (0.05, 0.2, 0.37, 0.9):
        assert _solve(policy, corr_scale=corr).floor_verdict == "none", corr


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

    # 闸关 ⇒ 逐字回退：同一断面下决策仍是 open_long、仓位被抬到最小一格。
    off = SizingRuntime(_RULE, None, None, inst_id="DOGE-USDT-SWAP",
                        bar_seconds=_BAR_SECONDS, floor_policy=None)
    off._vol = 0.01
    off._correlation = lambda action, pre, c, ex: (None, 0.0)
    final_off, _, _ = off.resolve(decision, ctx, _FakeExecutor())
    assert final_off.action == "open_long" and final_off.size_fraction == 0.5


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
    assert set(coarse.erased) == {"brakeSlow", "contrarian", "corrScale"}
    assert coarse.gears == 2 and coarse.min_gear == 0.5
    assert describe_gear_resolution(0.05).erased == ()
    assert describe_gear_resolution(0.25).erased == ()
    assert describe_gear_resolution(0.0).erased == ()
    # `step=1.0`（每次全仓）：阶梯只有一格，任何缩减都不可能发生。
    assert set(describe_gear_resolution(1.0).erased) == {"brakeSlow", "contrarian", "corrScale"}


def test_gear_note_is_absent_when_nothing_is_erased():
    assert gear_resolution_note(describe_gear_resolution(0.05)) is None
    note = gear_resolution_note(describe_gear_resolution(0.5))
    assert note is not None and "回撤刹车第一档" in note and "逆势通路强制降档" in note


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
