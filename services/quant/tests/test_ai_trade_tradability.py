"""可交易性预检纯核心（ai-trade-tradability-and-open-funnel 轴 A · G-1 / G-2 / G-2b / G-5）。

本文件的重心是 **G-2 的同构对拍**：探针的「是否否决」必须与 `edge_gate.apply_edge_gate` 对同一组
输入的结论**逐个相等**。探针说「会被否 98%」而门禁实际放行，比不给探针更坏。
"""
from __future__ import annotations

import random

import pytest

from app.ai_auto.cost_model import CostBreakdown
from app.ai_auto.decider import TradeDecision
from app.ai_auto.edge_gate import apply_edge_gate, estimate_gross_edge_bps
from app.ai_auto.tradability import (
    DISCLAIMER,
    TradabilityInputs,
    build_advice,
    evaluate,
    required_bps,
    status_wire,
    stats_wire,
    verdict_wire,
    veto_rate,
)


def _cost(total_bps: float, confidence: str = "ok") -> CostBreakdown:
    """构造一个只关心 `total_bps` / `confidence` 的成本读数（其余项不参与本层判定）。"""
    return CostBreakdown(fee_open_bps=0.0, fee_close_bps=0.0, half_spread_bps=0.0,
                         impact_bps=0.0, crossing_legs=2, funding_carry_bps=None,
                         total_bps=total_bps, confidence=confidence)


def _inputs(atr_bps, **over) -> TradabilityInputs:
    base = dict(atr_bps=tuple(atr_bps), cost_bps_market=13.0, cost_bps_limit=8.5,
                k_min=1.5, atr_target_mult=1.0, stop_atr_mult=2.0,
                cost_confidence_market="ok", cost_confidence_limit="ok", degraded_mult=1.2)
    base.update(over)
    return TradabilityInputs(**base)


def _evaluate(inputs: TradabilityInputs, **over):
    kwargs = dict(infeasible_rate=0.80, marginal_rate=0.40, bar="1m", inst_id="BTC-USDT-SWAP")
    kwargs.update(over)
    return evaluate(inputs, **kwargs)


# ---------- G-1：四档 level 各一条；样本不足 / 成本缺席 ⇒ unknown（绝不回落 ok）----------
def test_infeasible_when_almost_every_bar_is_vetoed():
    """BTC 1m 的真实量级：ATR 中位 ≈5.6bps，门禁要 ≥19.5bps ⇒ 几乎全被否。"""
    verdict = _evaluate(_inputs([5.6] * 200),
                        alternatives=({"bar": "15m", "vetoRateMarket": 0.203,
                                       "costBpsMarket": 13.0, "level": "marginal"},))
    assert verdict.level == "infeasible"
    assert verdict.veto_rate_market == 1.0
    assert "开仓意图会被否决" in verdict.headline
    assert verdict.advice is not None and "时间级别" in verdict.advice


def test_marginal_between_the_two_thresholds():
    """一半断面被否 ⇒ 落在 [0.40, 0.80) ⇒ marginal。"""
    verdict = _evaluate(_inputs([5.0] * 100 + [50.0] * 100))
    assert verdict.level == "marginal"
    assert verdict.veto_rate_market == 0.5
    assert verdict.advice is not None and "偏紧" in verdict.advice


def test_ok_when_atr_comfortably_clears_the_required_edge():
    verdict = _evaluate(_inputs([60.0] * 200))
    assert verdict.level == "ok"
    assert verdict.veto_rate_market == 0.0
    assert verdict.advice is None          # ok ⇒ 不给建议（没有要治的病）


def test_unknown_never_falls_back_to_ok():
    """两条 `unknown` 通路：样本不足 / 市价成本缺席。**绝不**回落 `ok`（那会骗用户去开单）。"""
    assert _evaluate(_inputs([50.0] * 59)).level == "unknown"
    assert _evaluate(_inputs([50.0] * 200, cost_bps_market=None)).level == "unknown"
    verdict = _evaluate(_inputs([50.0] * 10))
    assert verdict.veto_rate_market is None and "算不出" in verdict.headline


def test_k_min_zero_means_the_gate_is_idle_not_that_everything_passes():
    """门禁空转（k=0）⇒ 没有分母 ⇒ `unknown` + 文案如实说「未启用」，不冒充 `ok`。"""
    verdict = _evaluate(_inputs([5.0] * 200, k_min=0.0))
    assert verdict.level == "unknown"
    assert verdict.required_bps_market is None


# ---------- G-2：与 `apply_edge_gate` 的同构对拍（confidence 两态都要取到）----------
def test_probe_verdict_matches_the_real_gate_on_random_inputs():
    """G-2：随机 200 组 `(atr, cost, k, mult, confidence)`，探针与门禁的结论**逐个相等**。

    对拍时显式固定 `take_profit_pct=None, prior_ev_bps=None`（P2-1）：门禁的分子还会再对
    OCO 止盈距离与信号先验 EV 取 `min`，两者都只会让分子**更小**、让否决**更多**——故探针给出的
    否决率是一个**乐观下界**，真实否决率只会 ≥ 探针读数。这个方向是刻意选的（探针宁可说「偏紧」
    也不该说「不可行」而把用户吓退），但对拍必须把这两项摘掉才谈得上「同构」。

    `confidence` **必须在两态上都取到**：`degraded_multiplier` 在探针路径上恒定生效，只随机数值而
    把枚举固定在 happy path，是本仓库的成文测试盲区（P0-1 就是这么漏掉的）。
    """
    rng = random.Random(20260812)
    seen = set()
    for _ in range(200):
        atr_bps = rng.uniform(0.5, 120.0)
        cost_bps = rng.uniform(1.0, 40.0)
        k_min = rng.uniform(0.1, 4.0)
        mult = rng.uniform(0.2, 3.0)
        confidence = rng.choice(("ok", "degraded"))
        degraded_mult = rng.uniform(1.0, 2.0)
        seen.add(confidence)
        # 门禁那一侧：由 ATR 价格与参考价反推出同一个 `atr_bps`（`ref_px=1e4` ⇒ atr_px 即 bps）。
        edge = estimate_gross_edge_bps(action="open_long", ref_px=1e4, atr_px=atr_bps,
                                       take_profit_pct=None, atr_target_mult=mult,
                                       prior_ev_bps=None)
        decided, reason = apply_edge_gate(
            TradeDecision(action="open_long", size_fraction=0.5, confidence=0.9, reason="r"),
            edge, _cost(cost_bps, confidence), k_min=k_min, degraded_mult=degraded_mult)
        gate_vetoed = reason is not None
        assert decided.action == ("hold" if gate_vetoed else "open_long")
        # 探针那一侧：同一组数字喂进**判据本身**（`required_bps` + `veto_rate`），看它对这一根
        # 断面算出的否决率是 1.0 还是 0.0。刻意直接打这两个纯函数而不是走 `evaluate`——后者带着
        # 「样本 < 60 ⇒ unknown」的窗口门禁，单根断面上恒不可判，会把对拍变成一句空话。
        required = required_bps(_cost(cost_bps, confidence), k_min=k_min,
                                degraded_mult=degraded_mult)
        probe_rate = veto_rate((atr_bps,), mult, required)
        assert probe_rate == (1.0 if gate_vetoed else 0.0), (
            f"drift at atr={atr_bps} cost={cost_bps} k={k_min} mult={mult} conf={confidence}")
    assert seen == {"ok", "degraded"}      # 两态都真的被取到过


# ---------- G-2b：`degraded_multiplier` 真的被复用了（而不是自己写了一份）----------
def test_required_bps_includes_the_degraded_multiplier():
    """`degraded` 的成本读数下，分母必须是 `k × cost × degraded_mult`，**不是** `k × cost`。"""
    ok = required_bps(_cost(13.0, "ok"), k_min=1.5, degraded_mult=1.2)
    degraded = required_bps(_cost(13.0, "degraded"), k_min=1.5, degraded_mult=1.2)
    assert ok == pytest.approx(19.5)
    assert degraded == pytest.approx(23.4)


def test_required_bps_tracks_the_configured_multiplier():
    """把 `degraded_mult` 从 1.2 改成 1.5，分母必须跟着变——这条直接钉死「复用而非另写一份」。"""
    verdict12 = _evaluate(_inputs([50.0] * 200, cost_confidence_market="degraded",
                                  degraded_mult=1.2))
    verdict15 = _evaluate(_inputs([50.0] * 200, cost_confidence_market="degraded",
                                  degraded_mult=1.5))
    assert verdict12.required_bps_market == pytest.approx(23.4)
    assert verdict15.required_bps_market == pytest.approx(29.25)
    assert verdict12.degraded_mult_applied == 1.2 and verdict15.degraded_mult_applied == 1.5


def test_degraded_multiplier_is_not_applied_when_confidence_is_ok():
    verdict = _evaluate(_inputs([50.0] * 200, cost_confidence_market="ok", degraded_mult=1.2))
    assert verdict.degraded_mult_applied == 1.0
    assert verdict.required_bps_market == pytest.approx(19.5)


# ---------- 建议规则链 ----------
def test_infeasible_advice_recommends_a_longer_bar_when_one_is_feasible():
    verdict = _evaluate(_inputs([5.0] * 200),
                        alternatives=({"bar": "15m", "vetoRateMarket": 0.203,
                                       "costBpsMarket": 13.0, "level": "marginal"},))
    assert verdict.advice is not None
    assert "15m" in verdict.advice and "换预设解决不了" in verdict.advice


def test_infeasible_advice_says_so_when_no_bar_works():
    verdict = _evaluate(_inputs([5.0] * 200), alternatives=())
    assert verdict.advice is not None and "换标的" in verdict.advice


def test_advice_is_none_for_ok_and_unknown():
    assert build_advice(_evaluate(_inputs([60.0] * 200))) is None
    assert build_advice(_evaluate(_inputs([60.0] * 10))) is None


# ---------- 缺席语义：算不出一律 None，绝不填 0 冒充 ----------
def test_stop_band_block_is_absent_when_atr_stop_is_not_armed():
    verdict = _evaluate(_inputs([5.0] * 200, stop_atr_mult=0.0))
    assert verdict.stop_bps_p50 is None
    assert verdict.stop_inside_cost_rate_market is None
    assert verdict_wire(verdict)["stopBps"] is None


def test_stop_inside_cost_rate_reproduces_the_measured_shape():
    """BTC 1m 实测形状：`2×ATR` 的止损距离中位 11.2bps < 市价成本 13bps ⇒ 落进成本带。"""
    verdict = _evaluate(_inputs([5.6] * 200))
    assert verdict.stop_bps_p50 == pytest.approx(11.2)
    assert verdict.stop_inside_cost_rate_market == 1.0


# ---------- G-5：文案与免责只在后端出现一次 ----------
def test_wire_always_carries_the_disclaimer_with_the_real_sample_count():
    wire = verdict_wire(_evaluate(_inputs([5.0] * 123)), inst_id="BTC-USDT-SWAP", bar="1m",
                        source="cache", as_of_ts=1786600000)
    assert wire["disclaimer"] == DISCLAIMER.format(samples=123)
    assert wire["source"] == "cache" and wire["asOfTs"] == 1786600000
    assert "最近 123 根" in wire["headline"]


def test_no_return_promises_in_any_generated_copy():
    """文案纪律（G-20）：不得出现「已校准 / 经回测验证 / 稳定盈利」等收益承诺措辞。"""
    verdict = _evaluate(_inputs([5.0] * 200),
                        alternatives=({"bar": "1H", "vetoRateMarket": 0.008,
                                       "costBpsMarket": 13.2, "level": "ok"},))
    text = f"{verdict.headline}{verdict.advice}{DISCLAIMER}"
    for banned in ("已校准", "经回测验证", "稳定盈利"):
        assert banned not in text


def test_generated_copy_is_plain_text_not_markdown():
    """三串文案直接进 JSX 文本节点（`TradabilityCard` / 面板红条 / 启动弹窗），**不经 markdown**。

    终审逮到的复发缺陷：`build_advice` 里写过一句 `**换预设解决不了**`，而三个渲染点都是纯文本
    子节点 ⇒ 用户看到的是两个字面星号。本仓库为「JSX 里的 `**` 显示成字面星号」栽过一次，
    故把它固化成判据而不是一句注释。强调一律用「」与句式。
    """
    for atr in ([5.0] * 200, [20.0] * 200, [60.0] * 200):
        verdict = _evaluate(_inputs(atr),
                            alternatives=({"bar": "1H", "vetoRateMarket": 0.008,
                                           "costBpsMarket": 13.2, "level": "ok"},))
        for text in (verdict.headline, verdict.advice or "", DISCLAIMER):
            assert "**" not in text, text
    # 「所有周期都不可行」那一支（无可推荐周期）同样受判据约束。
    assert "**" not in (build_advice(_evaluate(_inputs([5.0] * 200)), ()) or "")


def test_status_and_stats_projections_stay_thin_and_consistent():
    """`status` / 落库两个投影**同源**于一份 wire（两处各算一份必然漂移）。"""
    wire = verdict_wire(_evaluate(_inputs([5.0] * 200)), inst_id="BTC-USDT-SWAP", bar="1m")
    status = status_wire(wire)
    stats = stats_wire(wire)
    assert status["level"] == stats["level"] == wire["level"]
    assert status["vetoRateMarket"] == stats["vetoRateMarket"] == wire["vetoRate"]["market"]
    assert status_wire(None) is None and stats_wire(None) is None
