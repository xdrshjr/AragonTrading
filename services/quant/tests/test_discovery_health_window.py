"""体检窗口口径（discovery-asset-ledger §2.3 · AL-2）—— 含 **P0-1 老资产双向门禁**。

P0-1 的形状：窗口若从 ``now`` 固定回看 ``windowBars`` 根，资产一旦老过 ``windowBars × step``
（15m 下约 15.6 天），暖机段就恒为 0 根 ⇒ H2 恒触发 ⇒ **全部老资产永久 ``unknown``**；
而 ``unknown`` 按 D-26 是中性文案「暂不评级」，界面上**看不出任何异常**。
本文件的 ``test_old_asset_still_gets_a_full_warmup_segment`` 就是它唯一的机器判据。
"""
from __future__ import annotations

from app.config import Settings
from app.discovery import health

STEP = 900          # 15m
TS0 = 1_700_000_000


def _limits(**overrides) -> health.HealthLimits:
    return health.HealthLimits.of(Settings(**overrides))


def _series(check_end: int, bars: int) -> list[int]:
    return [check_end - (bars - 1 - i) * STEP for i in range(bars)]


def _split(mined_end_ts: int, limits: health.HealthLimits, *, bars: int | None = None):
    """区间取数含两端，故一组拿到的是 ``windowBars + 1`` 根（与 ``load_range_candles`` 一致）。"""
    check_end = TS0
    count = bars if bars is not None else limits.window_bars + 1
    window_start = health.group_window_start(check_end, STEP, limits)
    fresh_start = health.fresh_start_of(mined_end_ts, check_end, STEP, limits)
    return health.split_window(_series(check_end, count), fresh_start_ts=fresh_start,
                               window_start_ts=window_start, window_end_ts=check_end)


# ============================ P0-1：老资产不得永久静音 ============================
def test_old_asset_still_gets_a_full_warmup_segment() -> None:
    """``minedEndTs`` 比 ``now − windowBars × step`` 还早 ⇒ 暖机段恒有 ``warmup_bars`` 根。

    v1 口径（``freshStartTs = max(minedEndTs, checkStart)``）下这里必得 0 根并判 H2。
    """
    limits = _limits()
    ancient = TS0 - (limits.window_bars + 5_000) * STEP
    window = _split(ancient, limits)
    assert window.warmup_bars == limits.warmup_bars
    assert window.fresh_bars == limits.max_fresh_bars + 1      # 含 freshStartTs 自身那根
    assert health.window_reasons(window.fresh_bars, window.warmup_bars, limits) == []


def test_fresh_segment_never_grows_without_bound() -> None:
    """夹上界的第二个作用：fresh 段不随资产年龄无界增长（否则单次体检墙钟会爆）。"""
    limits = _limits()
    for age_bars in (limits.window_bars, 10 * limits.window_bars, 500 * limits.window_bars):
        window = _split(TS0 - age_bars * STEP, limits)
        assert window.fresh_bars <= limits.max_fresh_bars + 1


def test_recent_asset_keeps_only_the_data_born_after_mining() -> None:
    """新资产：fresh 段起点就是挖掘窗口右端（下界生效），绝不把训练集算进判级。"""
    limits = _limits()
    mined_end = TS0 - 500 * STEP
    window = _split(mined_end, limits)
    assert window.fresh_start_ts == mined_end
    assert window.fresh_bars == 501            # 含 mined_end 自身那根
    assert window.warmup_bars == limits.window_bars + 1 - 501


def test_group_fetch_length_is_constant() -> None:
    """组取数长度恒为 ``windowBars`` 根：夹上界之后左端不会越过组窗口左端（§2.3 的证明）。"""
    limits = _limits()
    check_end = TS0
    window_start = health.group_window_start(check_end, STEP, limits)
    for age in (0, 1_000, 100_000):
        fresh_start = health.fresh_start_of(TS0 - age * STEP, check_end, STEP, limits)
        assert fresh_start - limits.warmup_bars * STEP >= window_start


# ============================ H1 / H2 / H3 ============================
def test_thin_fresh_segment_is_unknown_not_a_verdict() -> None:
    limits = _limits()
    assert health.window_reasons(limits.min_fresh_bars - 1, limits.warmup_bars,
                                 limits) == ["H1"]


def test_data_gap_in_the_warmup_segment_raises_h2() -> None:
    """夹上界之后 H2 只可能由取数缺口触发，因此它同时是一条数据质量告警。"""
    limits = _limits()
    assert "H2" in health.window_reasons(limits.min_fresh_bars,
                                         limits.min_warmup_bars - 1, limits)


def test_h3_uses_the_fresh_segment_coverage_only() -> None:
    """**P1-3 的反向判据**：暖机段全 NaN、fresh 段全有限 ⇒ **不判** H3。

    用 ``SplitEvaluation.coverage``（全窗有限值占比，含暖机段 rolling NaN）当分母的实现，
    会在这里把暖机 NaN 的锅算到资产头上而误判 H3。
    """
    limits = _limits()
    fresh = {"coverage": 1.0, "samples": 500, "rankIc": 0.05, "ir": 0.9,
             "freshBars": limits.min_fresh_bars, "warmupBars": limits.warmup_bars}
    reasons = health.grade("factor", fresh, {"rankIc": 0.05},
                           {"minSamples": 200, "minCoverage": 0.6, "minAbsIc": 0.01,
                            "minAbsIr": 0.5}, limits)
    assert "H3" not in reasons


def test_low_fresh_coverage_raises_h3() -> None:
    limits = _limits()
    fresh = {"coverage": 0.2, "samples": 500, "rankIc": 0.05, "ir": 0.9,
             "freshBars": limits.min_fresh_bars, "warmupBars": limits.warmup_bars}
    reasons = health.grade("factor", fresh, {"rankIc": 0.05},
                           {"minSamples": 200, "minCoverage": 0.6}, limits)
    assert reasons == ["H3"]


def test_unknown_next_check_waits_until_enough_bars_accumulate() -> None:
    """``unknown`` 顺延到「能攒够」的时刻，而不是每个周期都来刷一条 ``unknown``。"""
    limits = _limits()
    missing = 40
    plan = health.SchedulePlan(status="unknown", now=TS0, step=STEP,
                               fresh_bars=limits.min_fresh_bars - missing)
    assert health.next_check_ts(plan, limits) == TS0 + max(int(limits.interval_s),
                                                           missing * STEP)
