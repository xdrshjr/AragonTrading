"""下单前复检的重要性判据（ai-trade-profit-lock-and-entry-unblock 轴 A）。

为什么要有这一层：旧判据 `CostRuntime.pre_submit_context_changed` 对资金费率做**逐位** `!=`，
而 OKX 的 `fundingRate` 约每 60 秒刷新一次、LLM 深度推理中位 75 秒——几乎每一轮在推理期间都会
读到一个「新」费率（差值约 0.003 bps），整笔开仓被作废。生产库最新一组 181 次开仓里 172 次死在
这里。纪律本身是对的（不能用过期事实下单），错的是比较对象：本模块把比较对象从「参数」换成
「参数导致的这笔开仓的往返成本」，只在**变贵且贵出容忍度**时才拦；另补一条旧判据完全没有的
市价单不利价格漂移复检。

本模块只放纯函数与一个无 IO 的小运行时；会话里只有构造一行与一段编排。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

REASON_COST = "pre_submit_context_changed"
REASON_DRIFT = "pre_submit_price_drift"

# 身份元组 `_execution_identity()` 的逐位名（顺序与 session 里的元组构造一致）。
_IDENTITY_KEYS = ("positionSeq", "side", "qty", "restingClOrdId")


@dataclass(frozen=True)
class PreSubmitVerdict:
    """下单前复检结论；`code=None` 表示放行。"""

    code: str | None
    readings: dict = field(default_factory=dict)


_PASS = PreSubmitVerdict(None, {})


def _finite(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def cost_materiality(frozen_bps: float | None, current_bps: float | None,
                     *, tolerance_bps: float) -> PreSubmitVerdict:
    """成本复检：只有「现在比冻结时更贵，且贵出 tolerance 以上」才拦。

    Args:
        frozen_bps: 本轮冻结断面下这笔开仓的往返成本。
        current_bps: 此刻参数下同一笔开仓的往返成本。
        tolerance_bps: 容忍的上升量。

    Returns:
        任一侧不可算 ⇒ 放行（fail-open，同 edge 门禁极性，readings 标 `costUnknown`）；变便宜
        永远放行；上升 > tolerance ⇒ `code=pre_submit_context_changed`。
    """
    frozen, current = _finite(frozen_bps), _finite(current_bps)
    if frozen is None or current is None:
        return PreSubmitVerdict(None, {"kind": "cost", "costUnknown": True})
    delta = current - frozen
    if delta <= tolerance_bps:
        return _PASS
    return PreSubmitVerdict(REASON_COST, {
        "kind": "cost", "frozenBps": round(frozen, 4), "currentBps": round(current, 4),
        "deltaBps": round(delta, 4), "toleranceBps": float(tolerance_bps)})


def drift_limit_bps(*, min_bps: float, atr_mult: float, atr_bps: float | None) -> float:
    """漂移阈值 = max(min_bps, atr_mult × atr_bps)；ATR 缺失 ⇒ min_bps。"""
    atr = _finite(atr_bps)
    if atr is None or atr <= 0:
        return float(min_bps)
    return max(float(min_bps), float(atr_mult) * atr)


def adverse_drift(*, side: str, ref_px: float, now_px: float | None,
                  limit_bps: float) -> PreSubmitVerdict:
    """价格漂移复检：只看不利方向（long 看上涨、short 看下跌）。

    `now_px` 不可用 / `ref_px <= 0` / 方向非法 ⇒ 放行（fail-open）。
    """
    ref, now = _finite(ref_px), _finite(now_px)
    if ref is None or ref <= 0 or now is None or now <= 0 or side not in ("long", "short"):
        return _PASS
    sign = 1.0 if side == "long" else -1.0
    drift = (now - ref) / ref * 1e4 * sign
    if drift <= limit_bps:
        return _PASS
    return PreSubmitVerdict(REASON_DRIFT, {
        "kind": "drift", "driftBps": round(drift, 2), "limitBps": round(float(limit_bps), 2),
        "refPx": ref, "nowPx": now})


def identity_readings(frozen: tuple | None, current: tuple) -> dict:
    """身份复检的读数：逐元素比较 `_execution_identity()` 元组，列出变了的那几位。

    返回码不变（仍是 `pre_submit_context_changed`，冻结集合不动），读数只为区分
    「身份变了」与「成本变了」——G1 若不达标时唯一的归因手段。
    """
    frozen = tuple(frozen or ())
    changed = [key for idx, key in enumerate(_IDENTITY_KEYS)
               if idx >= len(frozen) or idx >= len(current) or frozen[idx] != current[idx]]
    return {"kind": "identity", "changed": changed}


class PreSubmitRuntime:
    """会话级复检运行时：持有 settings 读数、上一条否决读数与三个计数（无 IO）。"""

    def __init__(self, settings):
        self.enabled = bool(getattr(settings, "ai_trade_pre_submit_materiality_enabled", True))
        self.tolerance_bps = float(getattr(settings, "ai_trade_pre_submit_cost_tolerance_bps", 1.0))
        self.drift_enabled = bool(getattr(settings, "ai_trade_pre_submit_drift_guard_enabled",
                                          True))
        self.drift_min_bps = float(getattr(settings, "ai_trade_pre_submit_drift_min_bps", 20.0))
        self.drift_atr_mult = float(getattr(settings, "ai_trade_pre_submit_drift_atr_mult", 0.5))
        self.last: dict | None = None
        self.identity_vetoes = 0
        self.cost_vetoes = 0
        self.drift_vetoes = 0

    def begin(self) -> None:
        """每次 `_prepare_open_context` 入口复位：上一轮的读数绝不带进本轮。"""
        self.last = None

    def note_identity(self, frozen: tuple | None, current: tuple) -> None:
        """身份复检否决时记读数与计数（返回码由调用方保持不变）。"""
        self.last = identity_readings(frozen, current)
        self.identity_vetoes += 1

    def check(self, cost, decision, *, bar_seconds: int, ref_px: float,
              now_px: float | None, atr_px: float | None) -> PreSubmitVerdict:
        """成本复检 → 市价开仓的不利漂移复检；命中即记 `last` 与计数。"""
        frozen, current = cost.pre_submit_cost_pair(decision, bar_seconds)
        verdict = cost_materiality(frozen, current, tolerance_bps=self.tolerance_bps)
        if verdict.code:
            self.cost_vetoes += 1
            self.last = verdict.readings
            return verdict
        if self.drift_enabled and getattr(decision, "ord_type", None) == "market":
            ref = _finite(ref_px)
            atr = _finite(atr_px)
            atr_bps = atr / ref * 1e4 if atr is not None and ref else None
            limit = drift_limit_bps(min_bps=self.drift_min_bps, atr_mult=self.drift_atr_mult,
                                    atr_bps=atr_bps)
            side = "long" if decision.action == "open_long" else "short"
            drift = adverse_drift(side=side, ref_px=ref_px, now_px=now_px, limit_bps=limit)
            if drift.code:
                self.drift_vetoes += 1
                self.last = drift.readings
                return drift
        return verdict
