"""波动率目标化仓位 + ATR 止损纯核心单测（vol-sizing-and-drawdown-brake §7.1 T-1~T-18 / T-11b / T-12b）。

纯函数、零 IO、零 LLM。重点守住四条**照着实现能跑能绿但功能静默失效**的判据：
- S-1 只收不放（属性测试，500 组随机输入）；
- S-12 凯利赔率与门禁的 edge 读数**零依赖**（源码级 AST 判据 + 数值判据）；
- [P1-2] 档位地板会把缩减整个吃掉，且这件事必须被观测到（`scaleErased`）；
- [P1-1]/[P1-6] 总闸②独立于总闸①、窗口容量在启动期就要拦。
"""
from __future__ import annotations

import ast
import math
import pathlib
import random

from app.ai_auto import sizing
from app.ai_auto.decider import TradeDecision
from app.ai_auto.edge_gate import _GATED_ACTIONS as EDGE_GATED_ACTIONS
from app.ai_auto.sizing import (
    SizingInputs,
    SizingRule,
    StopRule,
    annual_to_bar_vol,
    apply_size_fraction,
    check_window_capacity,
    kelly_fraction,
    parse_sizing_rule,
    parse_stop_rule,
    realized_vol_bar,
    resolve_size_fraction,
    resolve_stop_distance,
)
from app.config import Settings

_BAR_15M = 900


def _rule(**over) -> SizingRule:
    base = {"vol_target_annual": 0.20, "vol_window": 96, "kelly_lambda": 0.25,
            "f_max": 1.0, "exposure_cap_mult": 0.0, "corr_min_samples": 96}
    base.update(over)
    return SizingRule(**base)


def _inputs(**over) -> SizingInputs:
    base = {"action": "open_long", "model_fraction": 0.5, "p_win": 0.55, "cost_bps": 10.0,
            "stop_bps": 200.0, "take_bps": 400.0, "realized_vol_bar": 0.004,
            "brake_mult": 1.0, "corr_scale": 1.0, "step": 0.25}
    base.update(over)
    return SizingInputs(**base)


# ---------- T-1：只收不放（S-1 属性测试）----------
def test_never_enlarges_model_fraction():
    rng = random.Random(20260725)
    for _ in range(500):
        model = rng.choice([0.25, 0.5, 0.75, 1.0])
        out = resolve_size_fraction(_inputs(
            model_fraction=model,
            p_win=rng.choice([None, 0.3, 0.55, 0.9]),
            cost_bps=rng.choice([None, 1.0, 10.0, 80.0]),
            stop_bps=rng.choice([None, 50.0, 200.0, 900.0]),
            take_bps=rng.choice([None, 100.0, 400.0, 1800.0]),
            realized_vol_bar=rng.choice([None, 0.0005, 0.004, 0.02]),
            brake_mult=rng.choice([1.0, 0.5]), corr_scale=rng.choice([1.0, 0.6, 0.0]),
            step=0.0),                            # step=0 ⇒ 连续，剥离档位地板后才看得见真实单调性
            _rule(), _BAR_15M)
        assert out.fraction is not None
        assert out.fraction <= model + 1e-9, f"输出 {out.fraction} 放大了模型提议 {model}"


# ---------- T-2 / T-3：豁免矩阵与「永不改写 action」----------
def test_gated_actions_identical_to_edge_gate():
    assert sizing._GATED_ACTIONS == EDGE_GATED_ACTIONS


def test_sizing_never_rewrites_action():
    for action in ("open_long", "open_short", "close", "hold", "cancel_order"):
        decision = TradeDecision(action=action, size_fraction=0.75, confidence=0.5, reason="r")
        out = resolve_size_fraction(_inputs(action=action), _rule(), _BAR_15M)
        assert apply_size_fraction(decision, out).action == action


def test_non_gated_actions_pass_through_the_same_object():
    decision = TradeDecision(action="close", size_fraction=None, confidence=None, reason="r")
    out = resolve_size_fraction(_inputs(action="close"), _rule(), _BAR_15M)
    assert out.fraction is None
    assert apply_size_fraction(decision, out) is decision


# ---------- T-5：止损是标的价口径，绝不与保证金收益率互换 ----------
def test_stop_pct_is_underlying_not_margin():
    rule = StopRule(stop_atr_mult=1.5, stop_rr=2.0, max_sl_pct=0.20, max_tp_pct=0.50)
    plan = resolve_stop_distance(atr_px=300.0, ref_px=30_000.0, rule=rule)
    assert plan is not None
    # 标的口径：1.5 × 300 / 30000 = 1.5%。5× 杠杆下同一波动的**保证金收益率**是它的 5 倍（7.5%），
    # 两者相差恰一个 leverage——利润保护层的阈值全部是后者口径，混用即整层失准。
    assert math.isclose(plan.stop_pct, 0.015, rel_tol=1e-9)
    assert math.isclose(plan.stop_pct * 5.0, 0.075, rel_tol=1e-9)
    assert math.isclose(plan.take_pct, 0.03, rel_tol=1e-9)


# ---------- T-10：年化 ⇄ 单根折算 ----------
def test_vol_target_annual_to_bar_conversion():
    for bar_seconds, expected in ((900, 0.20 / math.sqrt(35040.0)),
                                  (3600, 0.20 / math.sqrt(8760.0)),
                                  (14400, 0.20 / math.sqrt(2190.0))):
        assert math.isclose(annual_to_bar_vol(0.20, bar_seconds), expected, rel_tol=1e-12)
    assert annual_to_bar_vol(0.0, 900) is None          # 关闭值 ⇒ 该项不参与 min
    assert annual_to_bar_vol(0.2, 0) is None


def test_realized_vol_needs_window_plus_one_close():
    # 需要 window+1 根收盘价才产出 window 个收益；差一根即如实标 insufficient_samples（不假数据）。
    closes = [100.0 * (1.0 + 0.01 * (-1) ** i) ** i for i in range(11)]
    assert realized_vol_bar(closes[:10], 10) == (None, "insufficient_samples")
    sigma, source = realized_vol_bar(closes, 10)
    assert source == "realized" and sigma is not None and sigma > 0
    # 完全不动的价格序列 ⇒ 样本方差为 0 ⇒ 不可用（绝不把 0 波动率当作「可以满仓」的通行证）。
    assert realized_vol_bar([100.0] * 11, 10) == (None, "insufficient_samples")
    # 非正价 / 缺数一律不猜。
    assert realized_vol_bar([100.0, 0.0, 101.0], 2) == (None, "insufficient_samples")


def test_vol_source_three_states():
    assert resolve_size_fraction(_inputs(realized_vol_bar=None), _rule(), _BAR_15M).vol_source \
        == "insufficient_samples"
    assert resolve_size_fraction(_inputs(), _rule(vol_target_annual=0.0),
                                 _BAR_15M).vol_source == "disabled"
    assert resolve_size_fraction(_inputs(), _rule(), _BAR_15M).vol_source == "realized"


# ---------- T-11 / T-12：凯利降级一律「不参与 min」，绝不归零、绝不否决 ----------
def test_kelly_missing_prior_is_not_binding():
    out = resolve_size_fraction(_inputs(p_win=None, model_fraction=1.0, realized_vol_bar=None,
                                        step=0.0), _rule(), _BAR_15M)
    assert out.kelly_status == "no_prior"
    assert out.f_kelly is None
    assert out.fraction == 1.0          # 两项都不参与 ⇒ f_core = f_max ⇒ 与模型提议取小


def test_kelly_negative_odds_is_not_binding():
    # 成本吃掉整个止盈距离 ⇒ b<=0 ⇒ no_bracket；那是最小 edge 门禁的判定域，在这里再判一次就是双重计费。
    out = resolve_size_fraction(_inputs(cost_bps=500.0, take_bps=400.0, realized_vol_bar=None,
                                        model_fraction=1.0, step=0.0), _rule(), _BAR_15M)
    assert out.kelly_status == "no_bracket"
    assert out.f_kelly is None
    assert out.fraction == 1.0


def test_kelly_missing_cost_and_disabled_lambda():
    assert kelly_fraction(p_win=0.6, take_bps=400.0, cost_bps=None,
                          stop_bps=200.0) == (None, "no_cost")
    assert resolve_size_fraction(_inputs(), _rule(kelly_lambda=0.0),
                                 _BAR_15M).kelly_status == "disabled"


# ---------- T-11b（S-12 源码级）：凯利绝不读门禁的 edge 读数 ----------
def test_kelly_never_reads_edge_gate():
    source = pathlib.Path(sizing.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "edge_gate" not in (node.module or "")
        if isinstance(node, ast.Import):
            assert all("edge_gate" not in alias.name for alias in node.names)
    assert "edge_bps" not in source, "sizing.py 不得出现门禁 edge 读数的标识符（S-12）"


# ---------- T-12b：赔率随本层挂出去的括号变，与门禁读数无关 ----------
def test_kelly_odds_track_the_placed_bracket():
    tight, _ = kelly_fraction(p_win=0.6, take_bps=400.0, cost_bps=10.0, stop_bps=200.0)
    wide, _ = kelly_fraction(p_win=0.6, take_bps=1200.0, cost_bps=10.0, stop_bps=200.0)
    deep, _ = kelly_fraction(p_win=0.6, take_bps=400.0, cost_bps=10.0, stop_bps=600.0)
    assert wide > tight > deep >= 0.0
    # `SizingInputs` 根本没有承载门禁 edge 的字段：构造它需要的只有括号、成本与胜率。
    assert not any("edge" in name for name in SizingInputs.__dataclass_fields__)


# ---------- T-16 / T-17：夹紧到既有红线 / ATR 不可用即逐字回退 ----------
def test_atr_stop_clamped_to_existing_caps():
    rule = StopRule(stop_atr_mult=1.5, stop_rr=2.0, max_sl_pct=0.20, max_tp_pct=0.50)
    plan = resolve_stop_distance(atr_px=30_000.0, ref_px=30_000.0, rule=rule)   # 1.5×ATR = 150%
    assert plan is not None
    assert math.isclose(plan.stop_pct, 0.20)
    assert math.isclose(plan.take_pct, 0.40)     # 2 × 0.20 = 0.40 ≤ 0.50，未再被 tp 上限截断


def test_atr_unavailable_falls_back_verbatim():
    rule = StopRule(stop_atr_mult=1.5, stop_rr=2.0, max_sl_pct=0.20, max_tp_pct=0.50)
    assert resolve_stop_distance(atr_px=float("nan"), ref_px=30_000.0, rule=rule) is None
    assert resolve_stop_distance(atr_px=0.0, ref_px=30_000.0, rule=rule) is None
    assert resolve_stop_distance(atr_px=300.0, ref_px=0.0, rule=rule) is None
    assert resolve_stop_distance(atr_px=300.0, ref_px=30_000.0, rule=None) is None


# ---------- T-18 / T-23：档位地板的分辨率与「反噬」观测量 ----------
def test_quantize_resolution_documented():
    out = resolve_size_fraction(_inputs(model_fraction=0.03, realized_vol_bar=None, p_win=None,
                                        step=0.25), _rule(), _BAR_15M)
    assert out.fraction == 0.25          # 任何 >0 的开仓意图至少落到最小一格


def test_quantize_floor_erases_brake_is_detected():
    out = resolve_size_fraction(_inputs(model_fraction=0.25, brake_mult=0.5,
                                        realized_vol_bar=None, p_win=None, step=0.25),
                                _rule(), _BAR_15M)
    assert out.fraction == 0.25          # 0.25 × 0.5 = 0.125 → 吸附回 0.25：第一档减速是纯 no-op
    assert out.fraction_raw == 0.125     # 但吸附前的解如实留痕
    assert out.scale_erased is True      # 且这件事必须被观测到（否则运维会认定刹车生效了）
    assert out.scale_source == "brake"


def test_scale_not_erased_with_finer_step():
    out = resolve_size_fraction(_inputs(model_fraction=0.5, brake_mult=0.5,
                                        realized_vol_bar=None, p_win=None, step=0.1),
                                _rule(), _BAR_15M)
    assert out.fraction == 0.3 or out.fraction == 0.2   # 0.25 吸附到 0.1 阶梯的最近一格
    assert out.scale_erased is False


# ---------- T-28（[P1-1]）：总闸②独立于总闸①，runbook L1 真的可达 ----------
def test_stop_rule_independent_of_sizing_switch():
    settings = Settings(ai_trade_sizing_enabled=False, ai_trade_stop_atr_mult=1.5)
    assert parse_sizing_rule(settings) is None
    stop = parse_stop_rule(settings)
    assert stop is not None and stop.stop_atr_mult == 1.5
    plan = resolve_stop_distance(atr_px=300.0, ref_px=30_000.0, rule=stop)
    assert plan is not None and plan.stop_pct > 0


def test_parse_sizing_rule_off_by_default():
    assert parse_sizing_rule(Settings()) is None
    assert parse_stop_rule(Settings()) is None


# ---------- T-24（[P1-6]）：窗口容量在启动期就要拦，而不是运行期静默 None ----------
def test_window_capacity_checked_at_startup():
    rule = _rule(vol_window=96, corr_min_samples=96)
    assert check_window_capacity(rule, 200) is None
    message = check_window_capacity(rule, 80)
    assert message is not None
    assert "97" in message and "primingBars" in message
    assert check_window_capacity(None, 10) is None      # 未装配 ⇒ 从不拦（零回归）
