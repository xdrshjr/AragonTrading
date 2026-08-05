"""判级 H4–H6、留存度与中文映射门禁前半（discovery-asset-ledger §2.5 / §7.3-4）。

两条**承重**用例：
- ``HEALTH_CODES`` 与 ``grade()`` 实际能返回的码集合**完全相等**——少一个码，前端那张中文表
  就会漏一项，用户在界面上看到裸英文（本仓库连续三次栽在这条上）。
- 策略轴 ``accept.oos.sharpe <= 0`` ⇒ ``retention`` 为 ``None`` 且 H5 跳过（评审 P1-6）：
  裸比值在负分母下方向反转，「越差的 fresh 夏普给出越高的留存度」，UI 上是一条更长的绿条。
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.discovery import health

FACTOR_THRESHOLDS = {"minSamples": 200, "minCoverage": 0.6, "minAbsIc": 0.03,
                     "minAbsIr": 0.5}
STRATEGY_THRESHOLDS = {"maxDrawdown": 0.35}


def _limits() -> health.HealthLimits:
    return health.HealthLimits.of(Settings())


def _factor_fresh(**overrides) -> dict:
    limits = _limits()
    return {"rankIc": 0.05, "ir": 0.9, "coverage": 0.95, "samples": 500,
            "freshBars": limits.min_fresh_bars, "warmupBars": limits.warmup_bars,
            **overrides}


def _strategy_fresh(**overrides) -> dict:
    limits = _limits()
    return {"tradeCount": 40, "sharpe": 1.2, "totalReturn": 0.2, "maxDrawdown": -0.1,
            "freshBars": limits.min_fresh_bars, "warmupBars": limits.warmup_bars,
            **overrides}


# ============================ 因子轴 ============================
def test_healthy_factor_has_no_reasons() -> None:
    assert health.grade("factor", _factor_fresh(), {"rankIc": 0.06, "ir": 1.0},
                        FACTOR_THRESHOLDS, _limits()) == []


@pytest.mark.parametrize("rank_ic", [0.0, -0.02])
def test_non_positive_rank_ic_is_h4(rank_ic: float) -> None:
    """方向已冻结，非正即反号——这是体检最该抓到的那一类。"""
    assert health.grade("factor", _factor_fresh(rankIc=rank_ic), {"rankIc": 0.06},
                        FACTOR_THRESHOLDS, _limits()) == ["H4"]


def test_strength_decay_is_h5() -> None:
    """强度低于 ``max(minAbsIc, 登记强度 × retain)``。"""
    reasons = health.grade("factor", _factor_fresh(rankIc=0.031),
                           {"rankIc": 0.20, "ir": 1.0}, FACTOR_THRESHOLDS, _limits())
    assert "H5" in reasons


def test_information_ratio_decay_is_h6() -> None:
    reasons = health.grade("factor", _factor_fresh(ir=0.1), {"rankIc": 0.04, "ir": 1.0},
                           FACTOR_THRESHOLDS, _limits())
    assert "H6" in reasons


def test_factor_retention_is_none_on_a_zero_denominator() -> None:
    """分母不可用一律 ``None``——**绝不**填 0 或 1 顶替（那是在编造一个数）。"""
    assert health.retention_of("factor", _factor_fresh(), {"rankIc": 0.0}) is None
    assert health.retention_of("factor", _factor_fresh(rankIc=0.06),
                               {"rankIc": 0.03}) == 2.0        # >1 落库不夹紧


# ============================ 策略轴 ============================
def test_thin_fresh_trades_is_unknown() -> None:
    assert health.grade("strategy_param", _strategy_fresh(tradeCount=1), {"sharpe": 1.0},
                        STRATEGY_THRESHOLDS, _limits()) == ["H1"]


def test_assembly_failure_is_unknown_and_keeps_the_raw_code() -> None:
    """``S0:*`` 原样落 ``reasons``（可运维），**绝不**用无帧回测的结果判级（R-10）。"""
    reasons = health.grade("strategy_combo",
                           _strategy_fresh(rejectReason="S0:missing_htf:4H"),
                           {"sharpe": 1.0}, STRATEGY_THRESHOLDS, _limits())
    assert reasons[0] == "H2" and reasons[1] == "S0:missing_htf:4H"


@pytest.mark.parametrize("fresh", [{"totalReturn": -0.1}, {"sharpe": -0.3}])
def test_losing_fresh_segment_is_h4(fresh: dict) -> None:
    assert health.grade("strategy_param", _strategy_fresh(**fresh), {"sharpe": 1.0},
                        STRATEGY_THRESHOLDS, _limits()) == ["H4"]


def test_sharpe_decay_is_h5() -> None:
    reasons = health.grade("strategy_param", _strategy_fresh(sharpe=0.3), {"sharpe": 2.0},
                           STRATEGY_THRESHOLDS, _limits())
    assert "H5" in reasons


def test_drawdown_blowout_is_h6() -> None:
    reasons = health.grade("strategy_param", _strategy_fresh(maxDrawdown=-0.6),
                           {"sharpe": 1.0, "maxDrawdown": -0.2}, STRATEGY_THRESHOLDS,
                           _limits())
    assert "H6" in reasons


def test_negative_accept_sharpe_skips_h5_and_nulls_retention() -> None:
    """**评审 P1-6**：v1 的裸比值在这里会给出一个 >1 的假留存度并把 H5 判反。"""
    fresh = _strategy_fresh(sharpe=0.1, totalReturn=0.01)
    accept = {"sharpe": -0.8}
    assert "H5" not in health.grade("strategy_param", fresh, accept, STRATEGY_THRESHOLDS,
                                    _limits())
    assert health.retention_of("strategy_param", fresh, accept) is None


# ============================ §7.3-3：样本不足时**没有任何路径**能产出 healthy ============
@pytest.mark.parametrize("kind", ["factor", "strategy_param", "factor_combo",
                                  "strategy_combo"])
@pytest.mark.parametrize("rank_ic", [0.5, 0.05, -0.05])
@pytest.mark.parametrize("sharpe", [3.0, 1.0, -1.0])
def test_thin_window_can_never_yield_healthy(kind: str, rank_ic: float,
                                             sharpe: float) -> None:
    """穷举判据组合：``freshBars`` 不足时一律 ``unknown``，绝不可能拿到健康结论。"""
    limits = _limits()
    fresh = {**_factor_fresh(rankIc=rank_ic), **_strategy_fresh(sharpe=sharpe),
             "freshBars": limits.min_fresh_bars - 1}
    reasons = health.grade(kind, fresh, {"rankIc": 0.05, "sharpe": 1.0},
                           {**FACTOR_THRESHOLDS, **STRATEGY_THRESHOLDS}, limits)
    status, _streak = health.next_status(0, reasons, fail_streak_gate=limits.fail_streak)
    assert "H1" in reasons and status == "unknown"


# ============================ 中文映射门禁（前半） ============================
def test_health_codes_cover_every_code_grade_can_return() -> None:
    """穷举六种触发条件，断言 ``grade`` 的返回码集合 == ``HEALTH_CODES``。"""
    limits = _limits()
    produced: set[str] = set()
    cases = [
        ("factor", _factor_fresh(freshBars=1), {"rankIc": 0.06}, FACTOR_THRESHOLDS),
        ("factor", _factor_fresh(warmupBars=1), {"rankIc": 0.06}, FACTOR_THRESHOLDS),
        ("factor", _factor_fresh(coverage=0.1), {"rankIc": 0.06}, FACTOR_THRESHOLDS),
        ("factor", _factor_fresh(rankIc=-0.01), {"rankIc": 0.06}, FACTOR_THRESHOLDS),
        ("factor", _factor_fresh(rankIc=0.031), {"rankIc": 0.20}, FACTOR_THRESHOLDS),
        ("factor", _factor_fresh(ir=0.05), {"rankIc": 0.04}, FACTOR_THRESHOLDS),
        ("strategy_param", _strategy_fresh(tradeCount=0), {"sharpe": 1.0},
         STRATEGY_THRESHOLDS),
        ("strategy_param", _strategy_fresh(sharpe=-1.0), {"sharpe": 1.0},
         STRATEGY_THRESHOLDS),
        ("strategy_param", _strategy_fresh(sharpe=0.2), {"sharpe": 2.0},
         STRATEGY_THRESHOLDS),
        ("strategy_param", _strategy_fresh(maxDrawdown=-0.9), {"sharpe": 1.0},
         STRATEGY_THRESHOLDS),
        ("strategy_combo", _strategy_fresh(rejectReason="S0:gate_leak"), {"sharpe": 1.0},
         STRATEGY_THRESHOLDS),
    ]
    for kind, fresh, accept, thresholds in cases:
        produced.update(code for code in health.grade(kind, fresh, accept, thresholds,
                                                      limits)
                        if code in health.HEALTH_CODES)
    assert produced == set(health.HEALTH_CODES)
    assert set(health.HEALTH_LABELS) == set(health.HEALTH_CODES)
