"""因子 / 组合规格解析与校验（镜像 strategies/combo_specs.py）(spec §3.1)。

边界处一次性显式校验（C1）：未知 factorKey / 非法 direction / 成员数越界一律 400，绝不让配置
错误流到回测期被「安静空仓」掩盖。组合成员方向必须**冻结为 long/short**（``auto`` 在此被拒，
评审 C-P1-1）；训练权重经 ``apply_weights`` 烘焙进成员的 ``weight`` 字段，形成自包含快照。
"""
from __future__ import annotations

import json

from fastapi import HTTPException

from ..db.models import FactorSet
from ..engine.factors import FACTOR_LIBRARY, member_names
from ..strategies.factor_score_strategy import DEFAULT_THRESHOLDS

MIN_FACTOR_SET_MEMBERS = 1    # 单因子也可成组合（直接用某个因子打分）
MAX_FACTOR_SET_MEMBERS = 12
VALID_DIRECTIONS = ("long", "short")


def _bad(code: str, message: str) -> HTTPException:
    return HTTPException(400, detail={"code": code, "message": message})


def validate_factor_key(key: str) -> None:
    """因子 key 必须在库内（边界一次性校验）。"""
    if key not in FACTOR_LIBRARY:
        raise _bad("FACTOR_UNKNOWN", f"未知因子：{key}")


def validate_members(members: list[dict]) -> list[dict]:
    """校验并归一化组合成员（factorKey 已知、direction 冻结、成员数在界内）。

    Returns:
        ``[{factorKey, params, weight, direction}]``（自包含，不引用 factors.id）。

    Raises:
        HTTPException: 400 —— 未知因子 / 非法方向（含 auto）/ 成员数越界。
    """
    if not isinstance(members, list):
        raise _bad("FACTOR_MEMBERS", "members 必须为数组")
    out: list[dict] = []
    for member in members:
        key = member.get("factorKey") if isinstance(member, dict) else None
        validate_factor_key(key or "")
        direction = member.get("direction", "long")
        if direction not in VALID_DIRECTIONS:
            raise _bad("FACTOR_DIRECTION", f"成员 {key} 方向须冻结为 long/short（不接受 {direction}）")
        out.append({"factorKey": key, "params": dict(member.get("params") or {}),
                    "weight": _safe_float(member.get("weight"), 0.0), "direction": direction})
    if not (MIN_FACTOR_SET_MEMBERS <= len(out) <= MAX_FACTOR_SET_MEMBERS):
        raise _bad("FACTOR_MEMBERS",
                   f"组合成员数须在 {MIN_FACTOR_SET_MEMBERS}..{MAX_FACTOR_SET_MEMBERS} 之间")
    return out


def _safe_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def normalize_thresholds(raw: dict | None) -> dict:
    """叠加默认阈值（缺字段回落默认，单一口径与策略 resolve_thresholds 一致）。"""
    return {**DEFAULT_THRESHOLDS, **(raw or {})}


def apply_weights(members: list[dict], weights: dict[str, float] | None) -> list[dict]:
    """把训练权重按成员名烘焙进各成员 ``weight``（自包含快照；缺权重保留原值）。"""
    if not weights:
        return [dict(member) for member in members]
    names = member_names(members)
    return [{**member, "weight": float(weights.get(name, member.get("weight", 0.0)))}
            for name, member in zip(names, members)]


def members_from_set_row(row: FactorSet) -> list[dict]:
    """解析 factor_set 行的成员快照（烘焙 model_json.weights，缺失回退空表）。"""
    try:
        members = json.loads(row.members_json or "[]")
    except json.JSONDecodeError:
        members = []
    if not isinstance(members, list):
        members = []
    try:
        model = json.loads(row.model_json or "{}")
    except json.JSONDecodeError:
        model = {}
    weights = model.get("weights") if isinstance(model, dict) else None
    return apply_weights(members, weights)


def thresholds_from_set_row(row: FactorSet) -> dict:
    """解析 factor_set 行 config_json 里的 thresholds（叠加默认）。"""
    try:
        config = json.loads(row.config_json or "{}")
    except json.JSONDecodeError:
        config = {}
    return normalize_thresholds(config.get("thresholds") if isinstance(config, dict) else None)
