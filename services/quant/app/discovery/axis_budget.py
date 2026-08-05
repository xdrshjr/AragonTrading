"""轴预算分配与上游库存让渡（discovery-strategy-axes §2.8）—— 纯函数、可单测。

**BL-9 承重**：上游库存不足时名额**整体让渡并如实标注**，绝不留「选了却不产候选」的空转轴。
让渡记录必须带 ``toAxis``——「让渡去了哪条轴」得落到界面上，不能让用户自己推。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .genomes import ALL_AXES

READINESS_REASON_CN: dict[str, str] = {
    "no_minable_template": "没有可挖的策略模板",
    "member_catalog_too_small": "因子成员目录不足 2 条",
    "upstream_pool_too_small": "常规策略池不足，等待上游产出",
    "not_selected": "本任务未启用该轴",
}
AXIS_LABEL_CN: dict[str, str] = {
    "factor": "因子", "factor_combo": "因子组合策略", "strategy_param": "常规策略",
    "strategy_combo": "策略轮动组合",
}


@dataclass
class AxisPlan:
    """一轮的轴名额分配结果（``shares`` 求和恒等于 ``count``）。"""

    shares: dict = field(default_factory=dict)
    yielded: dict = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not any(int(value) > 0 for value in self.shares.values())

    def to_dict(self) -> dict:
        return {"shares": {key: int(value) for key, value in self.shares.items()},
                "yielded": {key: dict(value) for key, value in self.yielded.items()}}


def _largest_remainder(axes: list, weights: dict, count: int) -> dict:
    """最大余数法切名额（求和恒等于 ``count``；权重全 0 时退化为均分）。"""
    positive = {axis: max(0.0, float(weights.get(axis, 1.0))) for axis in axes}
    total = sum(positive.values())
    if total <= 0.0:
        positive = {axis: 1.0 for axis in axes}
        total = float(len(axes))
    exact = {axis: positive[axis] / total * count for axis in axes}
    base = {axis: int(math.floor(value)) for axis, value in exact.items()}
    remainder = count - sum(base.values())
    order = sorted(axes, key=lambda axis: (-(exact[axis] - base[axis]), axis))
    for axis in order[:max(0, remainder)]:
        base[axis] += 1
    return base


def _receiver(axes: list, readiness: dict) -> str:
    """让渡接收方 = **本 run 已选轴内**、按 ``ALL_AXES`` 次序排在最前的那条 ready 轴。

    评审 P1-8：必须限定在已选轴内——否则用户没勾 ``factor`` 时名额会被让给一条根本没启用的
    轴，等于凭空蒸发。
    """
    for axis in ALL_AXES:
        if axis in axes and bool(readiness.get(axis, (True, ""))[0]):
            return axis
    return ""


def plan_axis_budget(axes: list, weights: dict, count: int, readiness: dict) -> AxisPlan:
    """把一轮的候选名额切给各轴（最大余数法），并按上游库存做让渡。

    Args:
        axes: 本 run 已选轴（顺序不影响结果）。
        weights: 轴权重（缺省 1.0）。
        count: 本轮候选总名额。
        readiness: ``{axis: (ready: bool, reason: str)}``。

    Returns:
        ``AxisPlan``。全部轴都 not ready ⇒ 空计划，调用方跳过本轮并推 ``error`` 事件说明
        原因（**不**转 paused：库存会随下一轮因子轴产出而恢复）。
    """
    selected = [axis for axis in ALL_AXES if axis in set(axes)]
    if not selected or count <= 0:
        return AxisPlan(shares={axis: 0 for axis in selected})
    shares = _largest_remainder(selected, weights, int(count))
    receiver = _receiver(selected, readiness)
    plan = AxisPlan(shares=shares)
    if not receiver:
        return AxisPlan(shares={axis: 0 for axis in selected},
                        yielded={axis: {"count": shares[axis],
                                        "reason": readiness.get(axis, (False, ""))[1],
                                        "toAxis": ""} for axis in selected if shares[axis]})
    for axis in selected:
        ready, reason = readiness.get(axis, (True, ""))
        if ready or axis == receiver or shares[axis] <= 0:
            continue
        plan.yielded[axis] = {"count": int(shares[axis]), "reason": reason,
                              "toAxis": receiver}
        shares[receiver] += shares[axis]
        shares[axis] = 0
    return plan


def yield_sentence(axis: str, entry: dict) -> str:
    """让渡记录 → 中文一句话（BL-9 的 UI 落点与后端 note 共用同一措辞）。"""
    label = AXIS_LABEL_CN.get(axis, axis)
    target = AXIS_LABEL_CN.get(str(entry.get("toAxis") or ""), "")
    reason = READINESS_REASON_CN.get(str(entry.get("reason") or ""),
                                     str(entry.get("reason") or ""))
    tail = f" → {target}" if target else "（本轮无可接收的轴）"
    return f"{label} {int(entry.get('count', 0))}{tail}（原因：{reason}）"
