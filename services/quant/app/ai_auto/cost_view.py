"""决策前成本证据的装配纯核心（ai-trade-cost-truth-and-net-pnl 轴 D）。

从 `CostRuntime` 下沉出来的**纯函数**层：`CostBreakdown` + 费率快照 + 当前持仓 → `payload
["costAwareness"]` 的只读 wire。之所以单立一个模块而不是塞进 `cost_runtime.py`，理由与本仓库
既有的 `prior_service.py` / `profit_guard_bar.py` 同款——运行时那一层是**编排 + 网络 IO**，本层是
**可单测的纯装配**，混在一起会让「成本视图形状」的回归测试必须先造一个带凭证的运行时。

本模块**绝不自己估算成本**：`CostBreakdown` 由调用方从 `estimate_round_trip_cost` 取得，故 prompt
里的每一个 bps 与 edge 门禁真正据以判定的 bps 恒为同一个数（A-6 单一真源约束）。
"""
from __future__ import annotations

from .cost_model import CostBreakdown, position_break_even_px


def atr_share(total_bps: float, atr_px: float | None, last_px: float | None) -> float | None:
    """往返成本占当根 ATR 的比例；ATR / 参考价不可用 ⇒ `None`（文案据此省略该子句而非写「未知」）。"""
    if not atr_px or not last_px or last_px <= 0 or atr_px <= 0:
        return None
    atr_bps = float(atr_px) / float(last_px) * 1e4
    return round(float(total_bps) / atr_bps, 6) if atr_bps > 0 else None


def break_even_wire(position: dict | None, total_bps: float,
                    last_px: float | None) -> dict:
    """持仓的盈亏平衡价与距离；无持仓 ⇒ 两键为 `None`（**不是缺席**——形状恒定便于前端断言）。

    `breakEvenGapPct` 是**方向化**的「还差多少」：多头看 `(平衡价 − 现价)/现价`，空头取反，故两侧
    都满足「正数 = 尚未回本、负数 = 已覆盖成本」。不方向化会让空头持仓的文案说反。
    """
    side = (position or {}).get("side")
    px = position_break_even_px(position) if position else None
    if px is None:
        return {"breakEvenPx": None, "breakEvenGapPct": None}
    gap = None
    if last_px and float(last_px) > 0:
        raw = (px - float(last_px)) / float(last_px)
        gap = round(raw if side == "long" else -raw, 8)
    return {"breakEvenPx": px, "breakEvenGapPct": gap,
            "breakEvenBasis": "estimated"}


def funding_accrued_view(funding_stats: dict | None,
                         position: dict | None) -> dict | None:
    """本仓**已实际计提**的资金费只读键（horizon-acceptance §2.2.5）。

    与 `fundingCarryBps`（成本模型对未来的**预估**）刻意分开：那是「按目标持仓预计要付多少」，
    这是「这一仓到现在真的已经付了多少」。两者混成一个数会让模型分不清预估与实付。

    未装配账本 / 账本关 / 无持仓 / 本仓尚无计提读数 ⇒ **返回 `None` ⇒ 调用方让整键缺席** ⇒
    渲染管线整段省略 ⇒ prompt 逐字节回退（G-1 冻结断言可证）。

    **刻意不作为 `build_cost_view` 的第 10 个形参**：那个函数已远超 CLAUDE.md 的「形参 ≤5」，
    继续堆参只会更偏离；本层是纯装配，由 `CostRuntime` 在同一处把两块拼起来。
    """
    if not funding_stats or not funding_stats.get("enabled"):
        return None
    if (position or {}).get("side") not in ("long", "short"):
        return None
    open_usd = funding_stats.get("accruedOpenUsd")
    if open_usd is None:
        return None
    return {"accruedOpenUsd": open_usd,
            "crossings": funding_stats.get("crossings"),
            "intervalS": funding_stats.get("intervalS"),
            "intervalKnown": funding_stats.get("intervalKnown"),
            "lastRateBps": funding_stats.get("lastRateBps"),
            "basis": funding_stats.get("basis")}


def build_cost_view(cost: CostBreakdown, *, fee_snapshot, ord_type: str, side: str,
                    position: dict | None, last_px: float | None, atr_px: float | None,
                    session_fee_usd: float | None, net_basis: bool) -> dict:
    """§5.4 形状的 `costAwareness` wire（宪章章与每轮成本段共用**同一份**视图）。

    Args:
        cost: 本次预览的往返成本拆解（唯一真源 `estimate_round_trip_cost` 产出）。
        fee_snapshot: `FeeRateSnapshot | None`（决定 `source` / `level` 如实标注数从哪来）。
        ord_type: 预览假设的开仓腿类型（`"limit"` / `"market"`）。
        side: 预览假设的方向（`"long"` / `"short"`）。
        position: 当前持仓 payload（`None` = 空仓 ⇒ 平衡价两键为 `None`）。
        last_px: 本轮参考价。
        atr_px: 确认根 ATR（价格单位）。
        session_fee_usd: 本会话累计已付手续费（账本未装配 ⇒ `None` ⇒ 文案省略该子句）。
        net_basis: 会话盈亏是否已是净额口径（模型必须知道它看到的那个数字是毛是净）。

    Returns:
        只读 wire；**两个预览假设 `previewOrdType` / `previewSide` 必须显式下发**（P0-1）——文案要
        如实说明「按 X 腿、Y 方向估算」，否则模型会把一个假设当成事实。
    """
    view = {"roundTripBps": cost.total_bps, "roundTripPct": round(cost.total_bps / 1e4, 8),
            "modelVersion": cost.model_version,
            "liquidityAssumption": cost.liquidity_assumption,
            "feeSource": cost.fee_source,
            "hasPosition": bool(position and position.get("side") in ("long", "short")),
            "feeOpenBps": cost.fee_open_bps, "feeCloseBps": cost.fee_close_bps,
            "halfSpreadBps": cost.half_spread_bps, "impactBps": cost.impact_bps,
            "crossingLegs": cost.crossing_legs,
            "fundingCarryBps": cost.funding_carry_bps, "confidence": cost.confidence,
            # holding-horizon §2.7：期数随目标持仓时长变化（趋势持仓 3 天 ⇒ 9 期而非 1 期），
            # `fundingIntervalS is None` 表示**结算周期未知**（回落 8h 保守估算），绝不报一个确定值。
            "fundingPeriods": cost.funding_periods,
            "fundingIntervalS": cost.funding_interval_s,
            "fundingIntervalSource": cost.funding_interval_source,
            "fundingAssumedIntervalS": cost.funding_assumed_interval_s,
            "source": fee_snapshot.source if fee_snapshot is not None else "config",
            "level": fee_snapshot.level if fee_snapshot is not None else "",
            "previewOrdType": ord_type, "previewSide": side,
            "atrShare": atr_share(cost.total_bps, atr_px, last_px),
            "sessionFeeUsd": (None if session_fee_usd is None
                              else round(float(session_fee_usd), 6)),
            "netBasis": bool(net_basis)}
    view.update(break_even_wire(position, cost.total_bps, last_px))
    return view
