"""分段与跨段 purge 的纯核心单测（ai-trade-signal-prior-and-ev-gate §8.1 T-1/T-2）。

守两条：**切法与既有因子评估同源**（否则「IR ≥ x」这句话在两处含义不同），以及**跨段事件真的
被剔掉**（不剔的后果是 IR 与 t 值被系统性放大，而它们正是 `usable` 判据的依据）。
"""
from __future__ import annotations

from app.engine.factors.evaluation import DEFAULT_IC_SEGMENTS
from app.engine.walk_forward_purge import plan_segments, purge_crossing_events


def _segment_ir_bounds(m: int, segments: int) -> list[tuple[int, int]]:
    """**逐字复制** `evaluation._segment_ir` 的切法（`evaluation.py:120`），作为对拍基准。"""
    return [(j * m // segments, (j + 1) * m // segments) for j in range(segments)]


def test_plan_segments_matches_segment_ir_bounds():
    """T-1：切法与 `_segment_ir` 逐段相等（含不能整除的 199）。"""
    for n in (60, 199, 4000):
        assert plan_segments(n, DEFAULT_IC_SEGMENTS) == _segment_ir_bounds(
            n, DEFAULT_IC_SEGMENTS)


def test_plan_segments_is_contiguous_cover():
    """段首尾相接且恰好覆盖 `[0, n)`——留缝会让部分事件不属于任何一段而被静默丢弃。"""
    bounds = plan_segments(199, DEFAULT_IC_SEGMENTS)
    assert bounds[0][0] == 0 and bounds[-1][1] == 199
    assert all(a[1] == b[0] for a, b in zip(bounds, bounds[1:]))


def test_plan_segments_degenerate_inputs_return_empty():
    """退化入参不抛异常（调用方是离线 builder，`n=0` 只该产出空快照而不是 500）。"""
    assert plan_segments(0, 6) == []
    assert plan_segments(100, 0) == []
    assert plan_segments(-5, 6) == []


def test_purge_drops_events_whose_horizon_crosses_segment_end():
    """T-2：段末 `horizon + embargo` 内的事件被剔除，段内更早的事件原样保留。"""
    kept = purge_crossing_events([80, 88, 89, 95], seg_end=100, horizon_bars=8,
                                 embargo_bars=4)
    assert kept == [80, 88]          # 88+8+4=100 恰好压线保留；89 越界，95 更甚


def test_purge_with_zero_embargo_only_truncates_forward_window():
    """`embargo_bars = 0` ⇒ 只做前瞻窗口截断（隔离带是可关的，但截断不可关）。"""
    assert purge_crossing_events([90, 92, 93], seg_end=100, horizon_bars=8,
                                 embargo_bars=0) == [90, 92]


def test_purge_preserves_order_and_handles_empty():
    assert purge_crossing_events([], seg_end=100, horizon_bars=8, embargo_bars=12) == []
    assert purge_crossing_events([10, 20, 30], seg_end=100, horizon_bars=0,
                                 embargo_bars=0) == [10, 20, 30]
