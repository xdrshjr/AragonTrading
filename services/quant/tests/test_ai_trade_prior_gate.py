"""EV 分子升级的门禁单测（§8.1 T-11 / T-12 / T-13 / T-19；验收门禁 G-2 / G-10）。

**T-11 按产出级断言**（放行条件 3）：断言的是「开先验后被放行的轮次集合 ⊆ 关先验时被放行的集合」，
不是只比 `target_bps`。二者在 `required` 恒定时等价，但只要有人给 prior 分支单设一个更小的 `k`，
`target_bps` 层面的不变式仍然字面成立，而真实开仓反而变多——那正是本增量最危险的「缓解方案」。
"""
from __future__ import annotations

import random

from app.ai_auto.cost_model import CostQuery, estimate_round_trip_cost, resolve_cost_params
from app.ai_auto.decider import TradeDecision
from app.ai_auto.edge_gate import apply_edge_gate, estimate_gross_edge_bps, evaluate_edge_gate
from app.config import Settings

_SETTINGS = Settings()
_PARAMS = resolve_cost_params(_SETTINGS)
_PX = 10_000.0


def _cost(funding_rate: float | None = 0.0):
    return estimate_round_trip_cost(
        CostQuery(ord_type="market", side="long", bar_seconds=900,
                  funding_rate=funding_rate), _PARAMS)


def _open(action: str = "open_long") -> TradeDecision:
    return TradeDecision(action=action, size_fraction=0.5, confidence=0.7, reason="t")


def _estimate(*, atr_bps: float, tp_bps: float | None = None, prior=None, binding=False):
    """按 bps 反解入参构造估算（避免手写 dataclass 绕过真实路径）。"""
    return estimate_gross_edge_bps(
        action="open_long", ref_px=_PX, atr_px=_PX * atr_bps / 1e4,
        take_profit_pct=None if tp_bps is None else tp_bps / 1e4, atr_target_mult=1.0,
        prior_ev_bps=prior, prior_binding=binding)


def _is_veto(*, atr_bps, tp_bps, prior, binding, cost, k_min) -> bool:
    edge = _estimate(atr_bps=atr_bps, tp_bps=tp_bps, prior=prior, binding=binding)
    _, reason = apply_edge_gate(_open(), edge, cost, k_min=k_min, degraded_mult=1.2)
    return reason is not None


# ============================ T-11 / G-2：产出级 EV-1 单调保守 ============================
def test_ev1_veto_set_is_monotonically_wider_with_prior():
    """随机 200 组入参：绑定先验后的 veto 集合 ⊇ 未绑定时的 veto 集合（**绝不把 veto 翻成 pass**）。"""
    rng = random.Random(20260725)
    cost = _cost()
    flipped_to_veto = 0
    for _ in range(200):
        case = {"atr_bps": rng.uniform(1.0, 120.0),
                "tp_bps": rng.choice([None, rng.uniform(5.0, 200.0)]),
                "prior": rng.uniform(-30.0, 60.0),
                "cost": cost, "k_min": rng.uniform(0.1, 4.0)}
        off = _is_veto(binding=False, **case)
        on = _is_veto(binding=True, **case)
        assert not (off and not on), f"先验把一轮从 veto 翻成了 pass：{case}"
        flipped_to_veto += int(on and not off)
    assert flipped_to_veto > 0, "随机样本里一次都没收紧，本用例已失去证伪力"


def test_required_threshold_is_identical_across_sources():
    """放行条件 3：阈值公式**唯一**——同一个 `target_bps`，来源是 atr 还是 prior 判决完全一样。"""
    cost = _cost()
    for target in (5.0, 13.0, 26.1, 40.0):
        by_atr = _is_veto(atr_bps=target, tp_bps=None, prior=None, binding=False,
                          cost=cost, k_min=2.0)
        by_prior = _is_veto(atr_bps=999.0, tp_bps=None, prior=target, binding=True,
                            cost=cost, k_min=2.0)
        assert by_atr == by_prior


# ============================ T-12：source 标注 ============================
def test_source_marks_the_minimum_branch():
    assert _estimate(atr_bps=40.0).source == "atr"
    assert _estimate(atr_bps=40.0, tp_bps=20.0).source == "take_profit"
    edge = _estimate(atr_bps=40.0, tp_bps=20.0, prior=8.0, binding=True)
    assert edge.source == "prior" and edge.target_bps == 8.0


def test_negative_prior_keeps_its_sign():
    """`prior ≤ 0` 不特殊处理、**不夹到 0**：把「历史上亏 3.2bps」粉饰成「赚 0」是自欺。"""
    edge = _estimate(atr_bps=40.0, prior=-3.2, binding=True)
    assert edge.target_bps == -3.2 and edge.source == "prior"
    _, reason = apply_edge_gate(_open(), edge, _cost(), k_min=2.0, degraded_mult=1.2)
    assert reason is not None and "edge=-3.2bps" in reason


def test_prior_larger_than_existing_chain_never_widens():
    """先验比既有链更大时**不取它**——本增量对开仓是单调收紧的，绝不放大分子。"""
    edge = _estimate(atr_bps=20.0, prior=90.0, binding=True)
    assert edge.target_bps == 20.0 and edge.source == "atr"


# ============================ T-13：缺席回退 + ATR 不可用 ============================
def test_absent_prior_is_field_by_field_identical_to_pre_merge():
    base = _estimate(atr_bps=40.0, tp_bps=20.0)
    assert (base.target_bps, base.source, base.atr_bps, base.take_profit_bps) == (
        20.0, "take_profit", 40.0, 20.0)
    assert base.prior_ev_bps is None and base.would_target_bps == 20.0


def test_atr_unavailable_still_fails_open_even_with_negative_prior():
    """§3.7 末的显式取舍：ATR 不可用 ⇒ 整体 `None` ⇒ **放行**，即便先验说这组证据历史上亏钱。

    把先验提到 ATR 判空之前，等于在暖机断面上新开一条 fail-closed 路径，正是本层明令避免的
    「反思致永不开单」型事故。
    """
    assert estimate_gross_edge_bps(action="open_long", ref_px=_PX, atr_px=None,
                                   take_profit_pct=None, atr_target_mult=1.0,
                                   prior_ev_bps=-50.0, prior_binding=True) is None
    out = evaluate_edge_gate(_open(), settings=Settings(ai_trade_edge_gate_k_min=2.0,
                                                        ai_trade_funding_enabled=False),
                             last_px=_PX, atr_px=None, take_profit_pct=None, bar_seconds=900,
                             prior_ev_bps=-50.0, prior_binding=True)
    assert out.status == "unavailable" and out.reason is None
    assert out.wire["priorEvBps"] == -50.0        # 读数照发，即便本轮估不出分子
    assert out.wire["priorWouldVeto"] is None     # 但反事实不编造


# ============================ T-19 / G-10：读数与绑定解耦 ============================
def _evaluate(*, prior, binding, atr_px=_PX * 40.0 / 1e4):
    return evaluate_edge_gate(
        _open(), settings=Settings(ai_trade_edge_gate_k_min=2.0,
                                   ai_trade_funding_enabled=False),
        last_px=_PX, atr_px=atr_px, take_profit_pct=None, bar_seconds=900,
        prior_ev_bps=prior, prior_binding=binding)


def test_readout_is_unconditional_while_binding_is_gated():
    """总闸③关闭时：`edgeBps` / `edgeSource` 与先验缺席时逐字段相等，而反事实读数照发。"""
    absent = _evaluate(prior=None, binding=False)
    unbound = _evaluate(prior=3.0, binding=False)
    for key in ("edgeBps", "edgeSource", "requiredBps", "status", "reason"):
        assert unbound.wire[key] == absent.wire[key]
    assert unbound.wire["priorEvBps"] == 3.0
    assert unbound.wire["priorWouldTargetBps"] == 3.0
    # 反事实与真实判据同一个 `required`，不另算一份。
    assert unbound.wire["priorWouldVeto"] is (3.0 < unbound.wire["requiredBps"])


def test_counterfactual_matches_real_veto_once_bound():
    """L3 的「反事实对拍」：开闸后的真实 veto 应与开闸前记录的 `priorWouldVeto` 一致。"""
    for prior in (2.0, 30.0, 120.0):
        unbound = _evaluate(prior=prior, binding=False)
        bound = _evaluate(prior=prior, binding=True)
        assert unbound.wire["priorWouldVeto"] is (bound.reason is not None)


def test_prior_keys_absent_prior_are_none_not_missing():
    """先验缺席时三个键仍在 wire 上（值为 None）——运维据「键不见了」会误判成后端没上线。"""
    wire = _evaluate(prior=None, binding=False).wire
    assert wire["priorEvBps"] is None and wire["priorWouldTargetBps"] == wire["edgeBps"]
    assert wire["priorWouldVeto"] is not None      # 无先验时反事实 == 真实判据
