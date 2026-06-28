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


def norm_stats_from_set_row(row: FactorSet) -> dict:
    """解析 factor_set 行 model_json 里的 normStats（缺失为空，供 resolve_norm_contract 回落）（H2）。"""
    try:
        model = json.loads(row.model_json or "{}")
    except json.JSONDecodeError:
        model = {}
    stats = model.get("normStats") if isinstance(model, dict) else None
    return stats if isinstance(stats, dict) else {}


def provenance_from_set_row(row: FactorSet) -> dict:
    """解析 factor_set 行 model_json 里的 provenance（缺失为空，仅供 UI 展示训练区间，绝不参与打分）（L4）。"""
    try:
        model = json.loads(row.model_json or "{}")
    except json.JSONDecodeError:
        model = {}
    prov = model.get("provenance") if isinstance(model, dict) else None
    return prov if isinstance(prov, dict) else {}


def assert_norm_contract(thresholds: dict, model: dict | None) -> None:
    """已训练组合的 ``thresholds.normWindow`` 必须 == ``model.normStats.normWindow``，否则 400（H2）。

    缺 ``model`` / 无 ``normStats`` / 无 ``normWindow``（equal、未训练、旧组合）时跳过——只在确有训练契约时校验，
    兼容 partial update 与 equal 基线（评审 §8 误伤缓解）。

    Raises:
        HTTPException: 400 ``FACTOR_NORM_WINDOW_MISMATCH`` —— 阈值窗口与训练写入的归一化窗口漂移。
    """
    stats = model.get("normStats") if isinstance(model, dict) else None
    trained_window = stats.get("normWindow") if isinstance(stats, dict) else None
    if trained_window is None:
        return
    threshold_window = (thresholds or {}).get("normWindow")
    if threshold_window is not None and int(threshold_window) != int(trained_window):
        raise _bad("FACTOR_NORM_WINDOW_MISMATCH",
                   f"归一化窗口漂移：阈值 normWindow={threshold_window} 与训练 normStats.normWindow="
                   f"{trained_window} 不一致，请改用训练窗口或重新训练。")


def assert_weights_cover(members: list[dict], model: dict | None) -> None:
    """``model.weights`` 的键集必须 == 当前成员 ``member_names``，否则 400 ``FACTOR_WEIGHTS_STALE``（H3）。

    缺 ``model`` / 无 ``weights``（equal、未训练、partial update）时跳过——只在确有训练权重时校验。

    Raises:
        HTTPException: 400 ``FACTOR_WEIGHTS_STALE`` —— 成员已变化、训练权重陈旧，需重新训练。
    """
    weights = model.get("weights") if isinstance(model, dict) else None
    if not weights:
        return
    if set(weights.keys()) != set(member_names(members)):
        raise _bad("FACTOR_WEIGHTS_STALE",
                   "训练权重与当前成员不一致（成员已变化），请重新训练后再保存。")
