"""事件研究的分段与跨段 purge-embargo（ai-trade-signal-prior-and-ev-gate §3.4）。

事件研究里的泄漏不在「训练/测试」而在**段边界**：第 `i` 根的事件其前瞻窗口跨到了下一段，两段的
统计量就不再独立，IR 与 t 值会被系统性放大。本模块把「怎么切段」与「哪些事件不属于任何一段」两件
事钉成纯函数，供 `ai_auto/prior_builder` 唯一消费。

切法**逐字复制** `engine/factors/evaluation._segment_ir`（`j*m//segments`）：离线先验的段间 IR 与既有
因子评估必须是同一个量，否则「IR ≥ x」这句话在两处含义不同。绝不在此另立一套切分口径。

（评审裁决：`plan_purged_windows`（train/test 窗口规划）本轮**不做**——它没有消费方，而窗口的真实
形状要等 out-of-fold 先验那一轮才知道；先写一版没人用的实现，到时大概率推翻重写。）
"""
from __future__ import annotations


def plan_segments(n: int, segments: int) -> list[tuple[int, int]]:
    """把 `[0, n)` 等切成 `segments` 段，返回左闭右开区间列表（与 `_segment_ir` 逐字同源）。

    Args:
        n: 样本总根数。
        segments: 目标段数（`<= 0` 或 `n <= 0` ⇒ 空列表，退化输入不抛异常）。

    Returns:
        `[(i0, i1), …]`，升序、首尾相接、覆盖 `[0, n)`；空段（`i0 == i1`）保留在列表里，
        由调用方按「段内事件数」自行判定是否为有效段。
    """
    if n <= 0 or segments <= 0:
        return []
    return [(j * n // segments, (j + 1) * n // segments) for j in range(segments)]


def purge_crossing_events(indices: list[int], *, seg_end: int, horizon_bars: int,
                          embargo_bars: int) -> list[int]:
    """丢弃前瞻窗口越过段末的事件——**跨段的事件不属于任何一段**。

    判据 `i + horizon_bars + embargo_bars > seg_end` 里的 `embargo_bars` 是段末的隔离带：即便前瞻
    窗口恰好收在段内，紧邻段界的事件与下一段首批事件仍共享同一波行情，留着会让「段间一致性」
    这个量把自相关误读成稳健性。

    Args:
        indices: 段内候选事件的根索引（升序，调用方保证已落在该段内）。
        seg_end: 段的右开边界（`plan_segments` 产物的第二元）。
        horizon_bars: 前瞻根数 `h`。
        embargo_bars: 段末隔离根数（`0` ⇒ 关闭隔离，只做前瞻窗口截断）。

    Returns:
        保留下来的事件索引（原序）。
    """
    span = max(int(horizon_bars), 0) + max(int(embargo_bars), 0)
    return [i for i in indices if i + span <= seg_end]
