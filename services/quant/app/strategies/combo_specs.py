"""Shared saved-combo parsing helpers."""
from __future__ import annotations

import json

from fastapi import HTTPException

from ..db.models import Strategy
from ..engine.combo import ComboMember, ComboPolicy
from .base import get_strategy_class

MIN_COMBO_MEMBERS = 2
MAX_COMBO_MEMBERS = 8

COMBO_POLICY_KEYS = (
    "rebalanceBars",
    "lookbackBars",
    "selectionMetric",
    "minDwellBars",
    "switchMarginPct",
    "regimeGating",
    "turnoverBps",
)


def is_combo(template_key: str) -> bool:
    """Return whether a registry template is a combo strategy."""
    cls = get_strategy_class(template_key)
    return cls is not None and getattr(cls.meta, "kind", "atomic") == "combo"


def validate_combo_params(template_key: str, params: dict | None) -> None:
    """Validate saved-combo member count at create/update boundaries."""
    if not is_combo(template_key):
        return
    members = (params or {}).get("members", [])
    count = len(members) if isinstance(members, list) else 0
    if count < MIN_COMBO_MEMBERS or count > MAX_COMBO_MEMBERS:
        raise HTTPException(400, detail={
            "code": "COMBO_MEMBERS",
            "message": f"组合成员数须在 {MIN_COMBO_MEMBERS}..{MAX_COMBO_MEMBERS} 之间",
        })


def policy_from_spec(spec: dict | None) -> ComboPolicy:
    """Convert a camelCase policy dict into the engine ComboPolicy."""
    spec = spec or {}
    base = ComboPolicy()
    return ComboPolicy(
        rebalance_bars=int(spec.get("rebalanceBars", base.rebalance_bars)),
        lookback_bars=int(spec.get("lookbackBars", base.lookback_bars)),
        selection_metric=str(spec.get("selectionMetric", base.selection_metric)),
        min_dwell_bars=int(spec.get("minDwellBars", base.min_dwell_bars)),
        switch_margin=float(spec.get("switchMarginPct", base.switch_margin)),
        regime_gating=bool(spec.get("regimeGating", base.regime_gating)),
        turnover_bps=float(spec.get("turnoverBps", base.turnover_bps)),
    )


def build_members(
    members_spec: list[dict],
    notes: list[str],
    *,
    strict: bool = False,
) -> list[ComboMember]:
    """Instantiate combo members.

    Backtest preview/compare use best-effort mode. Paper startup uses strict
    mode so a saved combo cannot silently start with a different member set.
    """
    members: list[ComboMember] = []
    seen: dict[str, int] = {}
    for spec in members_spec:
        template_key = spec.get("templateKey") if isinstance(spec, dict) else None
        cls = get_strategy_class(template_key) if template_key else None
        if cls is None or getattr(cls.meta, "kind", "atomic") == "combo":
            message = f"成员模板 {template_key} 不可用"
            if strict:
                raise HTTPException(400, detail={"code": "UNAVAILABLE", "message": message})
            notes.append(f"{message}，已跳过")
            continue

        params = {**cls.meta.default_params, **(spec.get("params") or {})}
        count = seen.get(template_key, 0)
        seen[template_key] = count + 1
        display_key = template_key if count == 0 else f"{template_key}#{count + 1}"
        strategy = cls(params)
        members.append(ComboMember(
            key=display_key,
            template_key=template_key,
            name=spec.get("name") or cls.meta.name,
            strategy=strategy,
            timeframes=list(strategy.timeframes()),
        ))
    if len(members) < MIN_COMBO_MEMBERS:
        raise HTTPException(400, detail={
            "code": "COMBO_MEMBERS",
            "message": "组合至少需要 2 个可用成员",
        })
    return members


def combo_spec_from_row(row: Strategy) -> tuple[list[dict], dict]:
    """Resolve saved combo row params into member and policy specs."""
    cls = get_strategy_class(row.template_key)
    defaults = dict(cls.meta.default_params) if cls else {}
    try:
        params = {**defaults, **json.loads(row.params_json or "{}")}
    except json.JSONDecodeError:
        params = defaults
    members_spec = params.get("members", [])
    if not isinstance(members_spec, list):
        members_spec = []
    policy_spec = {key: params[key] for key in COMBO_POLICY_KEYS if key in params}
    return members_spec, policy_spec
