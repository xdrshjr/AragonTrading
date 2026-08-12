"""轴 B/C 纯核心：池化 vs 等权反例、结论表五态、覆盖率分母（G-6b / G-7 / G-11 / G-22）。"""
from __future__ import annotations

from app.ai_auto.calibration import calibration
from app.ai_auto.profitability import (
    VerdictGates,
    pooled,
    risk_profile,
    slice_by,
    slices,
    verdict,
)


def _trip(net: float, *, fee_known: bool = True, cost_bps: float | None = 7.0,
          move: float = 13.9, r: float | None = -0.01, **over) -> dict:
    row = {"netPnlUsd": net, "grossPnlUsd": net + 0.05, "roundTripFeeUsd": 0.05,
           "feeKnown": fee_known, "realizedCostBps": cost_bps if fee_known else None,
           "realizedGrossBps": (net + 0.05) / 145.0 * 1e4,
           "moveBps": move, "rMultiple": r, "stopBps": 500.0, "plannedRr": 2.0,
           "holdSeconds": 1271, "notionalUsd": 145.0, "costBasisAssumed": False}
    row.update(over)
    return row


def test_g7_pooled_win_rate_is_not_the_mean_of_per_group_rates():
    """G-7（D-2 回归）：1 组 1 笔全胜 + 1 组 10 笔 3 胜 ⇒ 池化 4/11=0.3636，等权均值是 0.65。"""
    trips = [_trip(1.0)] + [_trip(1.0) for _ in range(3)] + [_trip(-1.0) for _ in range(7)]
    assert pooled(trips)["winRate"] == round(4 / 11, 4)
    assert pooled(trips)["winRate"] != 0.65


def test_breakeven_payoff_is_paired_and_never_infinite():
    """约束 2：`winRate ∈ {0,1}` ⇒ `breakevenPayoff` 为 None（不造 ∞），且与 `payoff` 成对下发。"""
    all_wins = pooled([_trip(1.0), _trip(2.0)])
    assert all_wins["winRate"] == 1.0 and all_wins["breakevenPayoff"] is None
    mixed = pooled([_trip(1.0), _trip(-1.0), _trip(-1.0)])
    assert mixed["breakevenPayoff"] == round((1 - 1 / 3) / (1 / 3), 3)
    assert mixed["payoff"] == 1.0


def test_g6b_cost_readings_use_the_fee_subsample_not_the_whole_sample():
    """G-6b（[P0-4]）：90 笔无费率 + 10 笔带费 ⇒ `medianCostBps` 取那 10 笔，**不是 0.0**。"""
    trips = ([_trip(0.1, fee_known=False, cost_bps=None) for _ in range(90)]
             + [_trip(0.1, cost_bps=7.0) for _ in range(10)])
    out = pooled(trips)
    assert out["feeTrips"] == 10 and out["feeCoverage"] == 0.1
    assert out["medianCostBps"] == 7.0
    assert out["travelToCostRatio"] == round(13.9 / 7.0, 3)


def test_travel_to_cost_ratio_is_none_when_fee_coverage_is_a_rounding_error():
    """覆盖率 < 5% ⇒ 那个比值一律 None（一个 2% 子样本上的成本不构成全局结论）。"""
    trips = ([_trip(0.1, fee_known=False, cost_bps=None) for _ in range(99)]
             + [_trip(0.1, cost_bps=7.0)])
    out = pooled(trips)
    assert out["feeCoverage"] == 0.01 and out["travelToCostRatio"] is None
    assert out["medianCostBps"] == 7.0        # 读数本身照常给，只是不下那个结论


def test_g11_verdict_table_five_states_and_numbers_are_never_hidden():
    """G-11：结论表五态各一条；「样本不足」时 `pooled` 的数字**照常返回**。"""
    gates = VerdictGates(min_trips=30, t_threshold=2.0)
    small = pooled([_trip(0.1), _trip(-0.2)])
    assert verdict(small, gates)["code"] == "insufficient_samples"
    assert small["roundTrips"] == 2 and small["expectancyUsd"] is not None

    losers = pooled([_trip(-0.1 - i * 0.001) for i in range(40)])
    assert verdict(losers, gates)["code"] == "negative_significant"

    flat = pooled([_trip(0.1 if i % 2 else -0.1) for i in range(40)])
    assert verdict(flat, gates)["code"] == "no_edge_yet"

    slight = pooled([_trip(1.0 if i % 3 else -0.4) for i in range(40)])
    assert verdict(slight, gates)["code"] in ("positive_not_significant", "positive_significant")

    winners = pooled([_trip(0.5 + i * 0.001) for i in range(40)])
    assert verdict(winners, gates)["code"] == "positive_significant"


def test_g22_verdict_survives_a_none_t_stat():
    """G-22（[P1-6]）：`n=1` 与零方差两条分支各走一次，返回结论码而**不抛 TypeError**。"""
    gates = VerdictGates(min_trips=1, t_threshold=2.0)
    single = pooled([_trip(-0.5)])
    assert single["tStat"] is None
    assert verdict(single, gates)["code"] == "no_edge_yet"

    zero_var = pooled([_trip(0.5) for _ in range(10)])
    assert zero_var["tStat"] is None
    assert verdict(zero_var, gates)["code"] == "positive_not_significant"


def test_verdict_detail_repeats_the_denominators():
    """约束 7：结论的一句话必须复述分母（前端不拼数字）。"""
    trips = [_trip(-0.1, costBasisAssumed=True) for _ in range(40)]
    out = verdict(pooled(trips), VerdictGates(min_trips=30, t_threshold=2.0))
    assert "40 笔" in out["detail"] and "推定" in out["detail"] and "费率记录" in out["detail"]


def test_slices_carry_their_own_denominator_and_skip_missing_dimensions():
    """约束 1：每个切片行必带 `trips`；缺该维度的往返**不计入分母**（不是计入并填 0）。"""
    trips = [_trip(0.1, bar="15m"), _trip(-0.2, bar="15m"), _trip(0.3, bar=None)]
    rows = slice_by(trips, "bar")
    assert len(rows) == 1 and rows[0]["key"] == "15m" and rows[0]["trips"] == 2
    assert set(slices(trips)) == {"byBar", "byInstId", "byExitSource", "byPreset",
                                  "byHorizonProfile", "byConfidenceBucket"}


def test_confidence_slice_uses_the_frozen_buckets():
    """置信度切片按冻结桶边界归并（与三档预设的 min_confidence 对齐），而不是按取值散列。"""
    trips = [_trip(0.1, decisionConfidence=0.4), _trip(0.1, decisionConfidence=0.45),
             _trip(-0.1, decisionConfidence=0.8)]
    rows = {row["key"]: row["trips"] for row in slices(trips)["byConfidenceBucket"]}
    assert rows == {"0.35-0.5": 2, "0.7-0.9": 1}


def test_risk_profile_reports_r_denominator_and_stop_utilization():
    """D-1 的那张表：R 分布必带 `rSamples`，止损利用率是「位移 / 止损距离」。"""
    trips = [_trip(0.1, r=-0.011), _trip(-0.1, r=0.02), _trip(0.2, r=None)]
    out = risk_profile(trips)
    assert out["rSamples"] == 2 and out["reachedOneRShare"] == 0.0
    assert out["withinFifthRShare"] == 1.0
    assert out["stopUtilizationP50"] == round(13.9 / 500.0, 4)


def test_calibration_blocks_are_honest_about_zero_coverage():
    """R-7：上线当天估计侧 `coverage=0` 是**正确行为**——三块照常返回、`conclusive=false`。"""
    trips = [_trip(0.1) for _ in range(20)]
    out = calibration(trips, bucket_min_trips=10, model_constant_bps=13.0)
    assert out["edgeNumerator"]["n"] == 0 and out["edgeNumerator"]["conclusive"] is False
    assert out["edgeNumerator"]["medianRealizedGrossBps"] is not None   # 已实现侧照常有内容
    assert out["costModel"]["n"] == 20 and out["costModel"]["conclusive"] is True
    assert out["costModel"]["modelConstantBps"] == 13.0
    assert out["confidenceReliability"]["monotonic"] is None


def test_calibration_cost_block_excludes_fee_unknown_trips():
    """轴 C 问二的分母是 `feeTrips`；`note` 必须把那个分母说出来。"""
    trips = ([_trip(0.1, fee_known=False, cost_bps=None) for _ in range(90)]
             + [_trip(0.1, cost_bps=7.0) for _ in range(10)])
    block = calibration(trips, bucket_min_trips=10, model_constant_bps=13.0)["costModel"]
    assert block["n"] == 10 and block["coverage"] == 0.1
    assert block["medianRealizedCostBps"] == 7.0
    assert block["conclusive"] is True and "10" in (block["note"] or "")


def test_calibration_confidence_buckets_hide_under_sampled_rates():
    """桶内样本不足 ⇒ 该桶比例类读数一律 None（绝不用 2 笔冒充统计结论）。"""
    trips = [_trip(0.1, decisionConfidence=0.6) for _ in range(3)]
    buckets = calibration(trips, bucket_min_trips=10,
                          model_constant_bps=None)["confidenceReliability"]["buckets"]
    row = next(b for b in buckets if b["key"] == "0.5-0.7")
    assert row["trips"] == 3 and row["winRate"] is None and row["medianRMultiple"] is None


def test_empty_sample_never_raises():
    """§十.8：空列表在 `statistics.median` 上会抛，本模块跑在端点上，抛就是 500。"""
    assert pooled([])["roundTrips"] == 0
    assert risk_profile([])["rSamples"] == 0
    assert slices([])["byBar"] == []
    assert calibration([], bucket_min_trips=10, model_constant_bps=None)["costModel"]["n"] == 0
    assert verdict(pooled([]), VerdictGates())["code"] == "insufficient_samples"
