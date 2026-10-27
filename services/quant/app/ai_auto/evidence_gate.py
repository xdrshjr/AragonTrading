"""实证准入闸的纯核心（ai-trade-evidence-gated-capital §3.3）。

它回答的问题只有一个：「这个模型在这个标的、这个方向上，过去到底赚不赚钱？」样本来自影子意图
账本（`shadow_ledger`），按「标的 × 执行周期 × 方向」分桶；证据下界 > 0 的桶才允许真实开仓。

与既有 `edge_gate` 的区别：后者的分子是 ATR，量的是「波动够不够付成本」；本层量的是「有没有赢过」。

极性：统计不可得 ⇒ 按「未证实」处理（fail-closed）。与第 1 轮成本复检的 fail-open 相反，是有意的：
本层 fail-closed 的后果恰好就是本层想要的行为（只记影子），且影子照记、证据照攒，会自动解除。

反手（持多提 `open_short`）被拦时降级为 `close`，保留退出腿（仓库不变式 X-1「离场永远优先于开仓」，
先例是 `turnover_discipline.apply_turnover_gate` 的同一断面）；空仓开仓与加仓被拦 ⇒ `hold`。

本模块全部是纯函数，无 IO、无时钟读取（`now` 一律由调用方传入）。
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .shadow_ledger import RefPolicy, ref_policy_from_settings
from .turnover_discipline import OPEN_REVERSE, REVERSE_MARKER, open_kind

POLICIES = ("strict", "balanced", "observe")
STATES = ("proven", "unproven", "disproven")
REASON_UNPROVEN = "evidence_unproven"
REASON_DISPROVEN = "evidence_disproven"

STATE_PROVEN, STATE_UNPROVEN, STATE_DISPROVEN = STATES
LANE_EXEMPT, LANE_PASS, LANE_SHADOW = "exempt", "pass", "shadow"
GATING_LIVE, GATING_SHADOW = "live", "shadow"
_OPEN_ACTIONS = ("open_long", "open_short")
_SIDE_OF_ACTION = {"open_long": "long", "open_short": "short"}


@dataclass(frozen=True)
class EvidenceRule:
    """已装配的实证准入规则（`ref` 决定哪一个版本的样本可以参与统计）。"""

    policy: str = "strict"
    min_samples: int = 10
    window: int = 30
    max_age_days: float = 30.0
    z: float = 1.0
    pooled_disprove: bool = True
    ref: RefPolicy = field(default_factory=RefPolicy)


@dataclass(frozen=True)
class BucketStats:
    """一个桶（或同方向合并样本）的统计与三态。"""

    n: int
    mean_bps: float | None
    se_bps: float | None
    lcb_bps: float | None
    ucb_bps: float | None
    win_rate: float | None
    state: str
    basis: str = "bucket"


@dataclass(frozen=True)
class EvidenceGateOutcome:
    """一次门禁裁决；`lane="exempt"` 时 `decision` 是入参原对象（`is` 相等）。"""

    decision: object
    reason: str | None
    lane: str
    state: str | None
    wire: dict


def side_of_action(action: str) -> str | None:
    """`open_long` ⇒ `long`；`open_short` ⇒ `short`；其余 ⇒ None。"""
    return _SIDE_OF_ACTION.get(action)


def select_samples(samples: Iterable[tuple[float, float]], rule: EvidenceRule, *,
                   now: float) -> list[float]:
    """按 `matured_wall` 升序截取「未过期且最近 `window` 笔」的 `net_bps`。"""
    horizon = float(now) - float(rule.max_age_days) * 86400.0
    kept = sorted((w, v) for w, v in samples if w >= horizon and _is_finite(v))
    return [v for _, v in kept[-int(rule.window):]]


def bucket_stats(samples: Sequence[float], rule: EvidenceRule) -> BucketStats:
    """均值 / 标准误（样本标准差 n−1）/ 置信界 / 胜率 与三态（不含合并证伪，见 `resolve_state`）。"""
    values = [float(v) for v in samples if _is_finite(v)]
    n = len(values)
    if n == 0:
        return BucketStats(0, None, None, None, None, None, STATE_UNPROVEN)
    mean = statistics.fmean(values)
    se = statistics.stdev(values) / math.sqrt(n) if n >= 2 else None
    lcb = mean - rule.z * se if se is not None else None
    ucb = mean + rule.z * se if se is not None else None
    win = sum(1 for v in values if v > 0) / n
    state = STATE_UNPROVEN
    if n >= rule.min_samples:
        low = lcb if lcb is not None else mean
        high = ucb if ucb is not None else mean
        state = (STATE_PROVEN if low > 0 else STATE_DISPROVEN if high < 0
                 else STATE_UNPROVEN)
    return BucketStats(n, mean, se, lcb, ucb, win, state)


def resolve_state(bucket: BucketStats, pooled: BucketStats | None,
                  rule: EvidenceRule) -> BucketStats:
    """合并证伪（只收紧）：桶未证实、合并样本已证伪 ⇒ 桶记已证伪、`basis="pooled"`。

    合并样本**永远不能**把一个桶判成已证实：别的标的上做多赚钱，不构成在这个标的上做多的证据。
    """
    if (bucket.state == STATE_UNPROVEN and rule.pooled_disprove and pooled is not None
            and pooled.state == STATE_DISPROVEN):
        return BucketStats(bucket.n, bucket.mean_bps, bucket.se_bps, bucket.lcb_bps,
                           bucket.ucb_bps, bucket.win_rate, STATE_DISPROVEN, basis="pooled")
    return bucket


def gating_of(state: str | None, policy: str) -> str:
    """此刻这个方向的开仓会不会下真单（前端不重算，由后端给出）。"""
    if policy == "observe" or state == STATE_PROVEN:
        return GATING_LIVE
    if state == STATE_DISPROVEN:
        return GATING_SHADOW
    return GATING_SHADOW if policy == "strict" else GATING_LIVE


def apply_evidence_gate(decision, stats: BucketStats | None, rule: EvidenceRule, *,
                        position_side: str) -> EvidenceGateOutcome:
    """门禁裁决（§3.3.2）。非开仓 / 仅观察 / 已证实 ⇒ 原对象直通。

    Args:
        decision: 本轮决策（已过 edge 门禁）。
        stats: 本方向桶统计（已合并证伪）；None ⇒ 统计不可得，按未证实处理。
        rule: 已装配规则。
        position_side: 当前投影立场（`executor.projected`），决定反手的降级产物。
    """
    if decision.action not in _OPEN_ACTIONS:
        return EvidenceGateOutcome(decision, None, LANE_EXEMPT, None, {})
    kind = open_kind(position_side, decision.action)
    state = stats.state if stats is not None else STATE_UNPROVEN
    blocked = gating_of(state, rule.policy) == GATING_SHADOW
    wire = _gate_wire(stats, rule, state=state, kind=kind,
                      lane=LANE_SHADOW if blocked else LANE_PASS)
    if not blocked:
        return EvidenceGateOutcome(decision, None, LANE_PASS, state, wire)
    params = _reason_params(stats, rule, state)
    if kind == OPEN_REVERSE:
        params.append(REVERSE_MARKER)
    prefix = REASON_DISPROVEN if state == STATE_DISPROVEN else REASON_UNPROVEN
    reason = f"{prefix}({','.join(params)})"
    wire["reason"] = reason
    action = "close" if kind == OPEN_REVERSE else "hold"
    return EvidenceGateOutcome(_downgrade(action, reason), reason, LANE_SHADOW, state, wire)


def _downgrade(action: str, reason: str):
    """与 `edge_gate._veto` 逐字段同形；延迟导入打破 `decider → prompts → *_text` 的潜在环。"""
    from .decider import TradeDecision

    return TradeDecision(action=action, size_fraction=None, confidence=None, reason=reason)


def _fmt(value: float) -> str:
    return f"{value:+.1f}bps"


def _reason_params(stats: BucketStats | None, rule: EvidenceRule, state: str) -> list[str]:
    if stats is None:
        return [f"n=0/{rule.min_samples}", "stats=unavailable"]
    if state == STATE_DISPROVEN:
        params = [f"n={stats.n}"]
        if stats.mean_bps is not None:
            params.append(f"mean={_fmt(stats.mean_bps)}")
        if stats.ucb_bps is not None:
            params.append(f"ucb={_fmt(stats.ucb_bps)}")
        if stats.basis == "pooled":
            params.append("basis=pooled")
        return params
    params = [f"n={stats.n}/{rule.min_samples}"]
    if stats.mean_bps is not None:
        params.append(f"mean={_fmt(stats.mean_bps)}")
    if stats.n >= rule.min_samples and stats.lcb_bps is not None:
        params.append(f"lcb={_fmt(stats.lcb_bps)}")
    return params


def _gate_wire(stats: BucketStats | None, rule: EvidenceRule, *, state: str,
               kind: str | None, lane: str) -> dict:
    return {"lane": lane, "state": state, "policy": rule.policy, "openKind": kind,
            "minSamples": rule.min_samples, **stats_wire(stats)}


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(float(value), digits)


def stats_wire(stats: BucketStats | None) -> dict:
    """桶统计的 camelCase wire（缺即 None，绝不补 0）。"""
    if stats is None:
        return {"n": 0, "meanBps": None, "seBps": None, "lcbBps": None, "ucbBps": None,
                "winRate": None, "basis": "bucket"}
    return {"n": stats.n, "meanBps": _round(stats.mean_bps), "seBps": _round(stats.se_bps),
            "lcbBps": _round(stats.lcb_bps), "ucbBps": _round(stats.ucb_bps),
            "winRate": _round(stats.win_rate, 4), "basis": stats.basis}


def evidence_rule_wire(rule: EvidenceRule | None) -> dict | None:
    """规则回执（`group_status` / `config_json`）；未装配 ⇒ None。"""
    if rule is None:
        return None
    return {"enabled": True, "policy": rule.policy, "minSamples": rule.min_samples,
            "window": rule.window, "z": rule.z, "maxAgeDays": rule.max_age_days,
            "pooledDisprove": rule.pooled_disprove, "refPolicy": rule.ref.version()}


def rule_from_settings(settings, *, policy: str | None = None) -> EvidenceRule:
    """按 settings 装配（不看总闸；端点预览与 `resolve_evidence_rule` 共用）。"""
    chosen = policy or settings.ai_trade_evidence_default_policy
    if chosen not in POLICIES:
        raise ValueError(f"evidenceGate.policy 必须是 {'/'.join(POLICIES)} 之一（收到 {chosen!r}）")
    return EvidenceRule(
        policy=chosen, min_samples=int(settings.ai_trade_evidence_min_samples),
        window=int(settings.ai_trade_evidence_window),
        max_age_days=float(settings.ai_trade_evidence_max_age_days),
        z=float(settings.ai_trade_evidence_z),
        pooled_disprove=bool(settings.ai_trade_evidence_pooled_disprove),
        ref=ref_policy_from_settings(settings))


def resolve_evidence_rule(raw: dict | None, settings) -> EvidenceRule | None:
    """请求体 → 规则。运维总闸关 ⇒ None；显式 `enabled=false` ⇒ None；缺省 ⇒ 按 settings 开启。

    Raises:
        ValueError: `policy` 不在 `POLICIES` 里（API 层 Pydantic 已先拦成 422，这里是纯核心自卫）。
    """
    if not getattr(settings, "ai_trade_evidence_gate_enabled", False):
        return None
    raw = raw or {}
    if raw.get("enabled") is False:
        return None
    return rule_from_settings(settings, policy=raw.get("policy") or None)


def _is_finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
