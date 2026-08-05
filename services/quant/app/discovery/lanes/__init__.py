"""Lane 子包（discovery-strategy-axes §2.2）：一条轴 = 生成 + 审查 + 验证 + 池。"""
from __future__ import annotations

from .base import (  # noqa: F401
    Lane,
    LaneContext,
    outcome_row,
    reject_reason_of,
    rejected_row,
    run_validation,
    trim_pool,
)
from .factor_lane import FactorLane  # noqa: F401
from .strategy_lane import StrategyLane  # noqa: F401

__all__ = ["Lane", "LaneContext", "FactorLane", "StrategyLane", "build_lanes",
           "run_validation", "rejected_row", "outcome_row", "trim_pool",
           "reject_reason_of"]


def build_lanes(axes, *, config: dict, momentum, llm, coverage: dict,
                catalog=None) -> dict:
    """按已选轴装配 Lane 字典（``{axis: Lane}``，顺序即 ``ALL_AXES`` 次序）。"""
    lanes: dict = {}
    for axis in axes:
        if axis == "factor":
            lanes[axis] = FactorLane(config=config, momentum=momentum, llm=llm,
                                     coverage=coverage)
        else:
            lanes[axis] = StrategyLane(kind=axis, config=config, momentum=momentum,
                                       llm=llm, catalog=catalog)
    return lanes
