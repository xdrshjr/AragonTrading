"""Build paper-runtime strategy specs from database strategy rows."""
from __future__ import annotations

import json
from dataclasses import dataclass

from fastapi import HTTPException

from ..db.models import Strategy
from ..strategies.base import BaseStrategy, get_strategy_class
from ..strategies.combo_specs import (
    build_members,
    combo_spec_from_row,
    is_combo,
    policy_from_spec,
)
from .live_combo import LiveComboStrategy
from .session import PAPER_WINDOW_MAX


@dataclass(frozen=True)
class PaperStrategySpec:
    strategy_id: int
    strategy_name: str
    strategy_kind: str
    strategy: BaseStrategy
    config_extra: dict


def build_paper_strategy_spec(row: Strategy) -> PaperStrategySpec:
    """Return a fully validated paper strategy runtime spec."""
    if is_combo(row.template_key):
        return _build_combo_spec(row)
    return _build_atomic_spec(row)


def _build_atomic_spec(row: Strategy) -> PaperStrategySpec:
    cls = get_strategy_class(row.template_key)
    if cls is None:
        raise HTTPException(400, detail={
            "code": "UNAVAILABLE",
            "message": f"strategy template unavailable: {row.template_key}",
        })
    try:
        params = {**cls.meta.default_params, **json.loads(row.params_json or "{}")}
    except json.JSONDecodeError:
        params = dict(cls.meta.default_params)
    return PaperStrategySpec(
        strategy_id=row.id,
        strategy_name=row.name or cls.meta.name,
        strategy_kind="atomic",
        strategy=cls(params),
        config_extra={"strategyKind": "atomic"},
    )


def _build_combo_spec(row: Strategy) -> PaperStrategySpec:
    cls = get_strategy_class(row.template_key)
    members_spec, policy_spec = combo_spec_from_row(row)
    members = build_members(members_spec, [], strict=True)
    policy = policy_from_spec(policy_spec)
    strategy = LiveComboStrategy(
        members=members,
        policy=policy,
        policy_spec=policy_spec,
        members_spec=members_spec,
    )
    if strategy.warmup() > PAPER_WINDOW_MAX - 5:
        raise HTTPException(400, detail={
            "code": "COMBO_WINDOW",
            "message": "组合 warmup 超出实时模拟窗口上限",
        })
    return PaperStrategySpec(
        strategy_id=row.id,
        strategy_name=row.name or (cls.meta.name if cls else "Combo"),
        strategy_kind="combo",
        strategy=strategy,
        config_extra={
            "strategyKind": "combo",
            "combo": True,
            "members": members_spec,
            "policy": policy_spec,
            "liveComboVersion": 1,
        },
    )
