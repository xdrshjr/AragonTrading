"""往返结算账本纯核心（ai-trade-realized-edge-calibration 轴 A）。

回答的是这套系统至今答不上来的一个问题：**开仓那一刻估计的 edge / 成本，和平仓那一刻真正实现的
位移 / 成本，差多少**。今天 `RoundTrip.cost_bps` / `edge_bps` 写下去之后全仓无任何读点（D-3），
而「已实现 bps」「R 倍数」这两个量在全仓根本不存在（D-1）——于是门禁的分子准不准、成本模型准不准、
止损距离与真实交易尺度是不是差两个数量级，全都无法证伪。

十条硬约束（改本文件前逐条读一遍）：

1. **口径单点**：`net_mode=True` ⇒ `trade["pnl"]` 已是净额、毛额 = `pnl + fee`；否则相反。与
   `attribution.record_close`（`attribution.py:71-75`）与 `exit_attribution.ExitLedger.record`
   逐字同构，本模块**绝不写第三种口径**。
1b. **成本口径是「往返双腿」**：`SettledTrip.fee_usd = open_fee_usd + close_fee`，与
   `RoundTrip.fee_usd`（**只记平仓腿**）**不同名不同义**，故 wire 键名用 `roundTripFeeUsd` 杜绝
   误读。沿用平仓腿会让 `realizedCostBps` 恰好只有追溯复算的一半，而轴 C 问二拿它去比**往返**
   成本估计，得到的「成本模型高估一倍」将完全是口径造成的假象。
2. **bps 分母恒为开仓名义**：`notional_usd = entry_px × qty × ct_val`；`ct_val == 0` ⇒ 全部 bps 量
   与 `risk_usd` / `r_multiple` **一律 None**，绝不填 0（0 会被聚合读成「成本为零」）。
2b. **「没记费率」必须与「费率为零」可区分**：任一腿缺 `fee` 键 / 非有限 ⇒ `fee_known=False` ⇒
   `fee_usd` / `realized_cost_bps` / `cost_error_bps` 一律 None。`fee == 0.0` 且该腿确实带了这个
   键 ⇒ `fee_known=True`（真实的零费率成交是合法样本）。
3. **`r_multiple` 缺任一输入即 None**：`stop_px` 缺席 / 止损距离为 0 / `ct_val == 0` 任一成立 ⇒
   None。一个被 0 兜底的 R 比没有 R 更糟。
4. **`stop_atr_mult` 的 ATR 取开仓那一刻的确认根 ATR**，经 `profit_guard.sanitize_atr` 归一
   （与 `edge_gate.py:109` 同一条降级判据）；不可用 ⇒ None。
5. **结算失败恒返回 None 且绝不抛**：本模块跑在真实资金成交的落库路径上（`_dispatch` →
   `_persist_trade`），任何异常都会中断成交落库。全部计算包在 `_finite()` 归一里，**无 `raise`
   语句**（用例 G-4 用 AST 断言）。
6. **`trip_meta_wire` 的键一律「算不出即省略」**，不是 `None`——缺键与 `null` 在老数据上必须表现
   一致，且省略键让 `explain_json` 在低价值往返上不膨胀。
7. **`exit_source` 只读、不判**：必须取 `explain["meta"]["exitSource"]`，那是
   `session._inject_explain_meta` 打的**唯一**标且受 `ai_trade_exit_attribution_enabled` 门控。
   本模块**绝不自己调 `classify_live`**：那会造出第二个分类现场（违反 `exit_attribution.py:11-14`
   的「单一收口打标」纪律）并绕过那个总闸。缺席 ⇒ `"unknown"`。
8. **`seq` 只能取 `OpenSnapshot.seq`**：`_dispatch` 的平仓分支是**先** `_position_seq += 1` **再**
   `record_close`，当轮读 `self._position_seq` 恒比本笔往返大 1。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .profit_guard import sanitize_atr

_ROUND_BPS = 4
_ROUND_USD = 6

# 待成交估计槽的容量与命名（[P0-2]）：限价开仓单 resting 期间估计寄存在这里，按 `clOrdId` 认领。
PENDING_ESTIMATE_MAX = 8


@dataclass(frozen=True)
class RoundEstimate:
    """一轮受门禁约束的开仓意图在**决策那一刻**的全部估计（轮级，绝不跨轮存活）。"""

    est_edge_bps: float | None       # edge_gate 的毛 edge 粗估（k_min<=0 ⇒ None）
    est_cost_bps: float | None       # cost_model 的往返成本
    edge_source: str | None          # atr | take_profit | prior
    cost_confidence: str | None      # ok | degraded
    decision_confidence: float | None
    wake_kind: str | None
    ord_type: str = "market"         # market | limit


@dataclass(frozen=True)
class OpenSnapshot:
    """开仓成交那一刻冻结的断面（`(side, seq)` 是 epoch 身份，与 profit_guard / horizon 同源）。"""

    seq: int
    side: str
    ts: int
    wall_ts: float
    entry_px: float
    qty: float
    notional_usd: float
    open_fee_usd: float              # 开仓腿费**累加**（部分成交多腿）
    open_fee_known: bool             # 任一开仓腿的 fee 缺失 ⇒ False ⇒ 全部成本类 bps 一律 None
    stop_px: float | None = None
    take_px: float | None = None
    atr_px: float | None = None
    estimate: RoundEstimate | None = None


@dataclass(frozen=True)
class SettleContext:
    """结算的全部环境量（口径开关 / 合约乘数 / 退出来源）。[P1-11] 形参聚合，满足 CLAUDE.md ≤5。"""

    net_mode: bool
    ct_val: float
    exit_source: str = "unknown"


@dataclass(frozen=True)
class SettledTrip:
    """一笔已结算往返。所有 bps 量的分母恒为**开仓名义**（`notional_usd`）。"""

    seq: int
    side: str
    entry_ts: int
    exit_ts: int
    hold_seconds: float
    entry_px: float
    exit_px: float
    notional_usd: float | None
    gross_pnl_usd: float
    net_pnl_usd: float
    fee_usd: float | None            # **往返双腿**腿费；`open_fee_known=False` ⇒ None
    fee_known: bool
    realized_gross_bps: float | None
    realized_net_bps: float | None
    realized_cost_bps: float | None
    move_bps: float | None
    risk_usd: float | None
    r_multiple: float | None
    planned_rr: float | None
    stop_atr_mult: float | None
    exit_source: str
    est_edge_bps: float | None = None
    est_cost_bps: float | None = None
    edge_source: str | None = None
    cost_confidence: str | None = None
    decision_confidence: float | None = None
    wake_kind: str | None = None
    edge_error_bps: float | None = None
    cost_error_bps: float | None = None


def build_round_estimate(out, decision, wake_kind: str | None) -> RoundEstimate | None:
    """把 `EdgeGateOutcome` + 本轮决策压成轮级估计；`out` 缺席 ⇒ None（门禁空转的轮不留估计）。

    **模块级纯函数**，故 `session.py` 里不必拼 dict（§3.1.5 的行数预算是硬判据）。
    """
    if out is None:
        return None
    # `EdgeGateOutcome` 只有 `cost_bps` / `edge_bps` 两个标量字段，来源与置信度住在 `wire`
    # （`edge_gate.py:230-242`）——**不要凭记忆去读 `out.edge.source`，那两个属性不存在**。
    wire = getattr(out, "wire", None) or {}
    return RoundEstimate(
        est_edge_bps=_finite(getattr(out, "edge_bps", None)),
        est_cost_bps=_finite(getattr(out, "cost_bps", None)),
        edge_source=_text(wire.get("edgeSource")),
        cost_confidence=_text(wire.get("costConfidence")),
        decision_confidence=_finite(getattr(decision, "confidence", None)),
        wake_kind=_text(wake_kind),
        ord_type=_text(getattr(decision, "ord_type", None)) or "market")


def open_snapshot(trade: dict, *, seq: int, now_wall: float, atr_px: float | None,
                  ct_val: float, estimate: RoundEstimate | None) -> OpenSnapshot | None:
    """开仓成交 → 冻结断面；价 / 量不可用 ⇒ None（调用方据此不建 epoch）。"""
    entry_px = _positive(trade.get("price"))
    qty = _positive(trade.get("qty"))
    if entry_px is None or qty is None:
        return None
    fee, fee_known = _leg_fee(trade)
    return OpenSnapshot(
        seq=int(seq), side=_text(trade.get("side")) or "", ts=int(trade.get("ts") or 0),
        wall_ts=float(now_wall or 0.0), entry_px=entry_px, qty=qty,
        notional_usd=entry_px * qty * max(0.0, float(ct_val or 0.0)),
        open_fee_usd=fee, open_fee_known=fee_known,
        stop_px=_positive(trade.get("stopLossPrice")),
        take_px=_positive(trade.get("takeProfitPrice")),
        atr_px=sanitize_atr(atr_px), estimate=estimate)


def merge_open_leg(snap: OpenSnapshot, trade: dict, ct_val: float) -> OpenSnapshot:
    """开仓**部分成交**的幂等续记：只累加 `qty` / `notional` / 腿费并按量加权更新 `entry_px`。

    绝不重置 `estimate` / `ts` / `wall_ts` / `stop_px`——那正是同一个 epoch 的身份所在（接缝 2）。
    任一腿缺 `fee` ⇒ `open_fee_known` 此后不可再翻真。
    """
    px = _positive(trade.get("price"))
    qty = _positive(trade.get("qty"))
    fee, fee_known = _leg_fee(trade)
    if px is None or qty is None:
        return snap
    total_qty = snap.qty + qty
    entry_px = ((snap.entry_px * snap.qty + px * qty) / total_qty) if total_qty > 0 else snap.entry_px
    return OpenSnapshot(
        seq=snap.seq, side=snap.side, ts=snap.ts, wall_ts=snap.wall_ts,
        entry_px=entry_px, qty=total_qty,
        notional_usd=entry_px * total_qty * max(0.0, float(ct_val or 0.0)),
        open_fee_usd=snap.open_fee_usd + fee,
        open_fee_known=bool(snap.open_fee_known and fee_known),
        stop_px=snap.stop_px, take_px=snap.take_px, atr_px=snap.atr_px,
        estimate=snap.estimate)


def settle(open_snap: OpenSnapshot, close_trade: dict, ctx: SettleContext) -> SettledTrip | None:
    """结算一笔往返；任一必需输入不可用 ⇒ **None 且绝不抛**（约束 5）。"""
    if open_snap is None or not isinstance(close_trade, dict):
        return None
    exit_px = _positive(close_trade.get("price"))
    if exit_px is None or open_snap.entry_px <= 0:
        return None
    pnl = _finite(close_trade.get("pnl")) or 0.0
    close_fee, close_fee_known = _leg_fee(close_trade)
    gross = pnl + close_fee if ctx.net_mode else pnl
    net = pnl if ctx.net_mode else pnl - close_fee
    fee_known = bool(open_snap.open_fee_known and close_fee_known)
    fee_usd = (open_snap.open_fee_usd + close_fee) if fee_known else None
    ct_val = max(0.0, float(ctx.ct_val or 0.0))
    notional = open_snap.entry_px * open_snap.qty * ct_val if ct_val > 0 else None
    unit = open_snap.qty * ct_val if ct_val > 0 else None
    stop_distance = (abs(open_snap.entry_px - open_snap.stop_px)
                     if open_snap.stop_px is not None else 0.0)
    risk_usd = (stop_distance * unit) if (unit is not None and stop_distance > 0) else None
    est = open_snap.estimate
    realized_gross_bps = _bps(gross, notional)
    realized_cost_bps = _bps(fee_usd, notional) if fee_usd is not None else None
    exit_ts = int(close_trade.get("ts") or 0)
    return SettledTrip(
        seq=open_snap.seq, side=open_snap.side, entry_ts=open_snap.ts, exit_ts=exit_ts,
        hold_seconds=max(0.0, float(exit_ts - open_snap.ts)),
        entry_px=open_snap.entry_px, exit_px=exit_px,
        notional_usd=_round(notional, _ROUND_USD),
        gross_pnl_usd=round(gross, _ROUND_USD), net_pnl_usd=round(net, _ROUND_USD),
        fee_usd=_round(fee_usd, _ROUND_USD), fee_known=fee_known,
        realized_gross_bps=realized_gross_bps, realized_net_bps=_bps(net, notional),
        realized_cost_bps=(None if realized_cost_bps is None else abs(realized_cost_bps)),
        # `move_bps` 是**纯价格比例**（`ct_val` 代数抵消），故它在 `ct_val` 未知的会话上照常可得
        # ——D-1 的那个量必须尽可能有分母，否则「位移撑不过成本」这条结论会没有样本。
        move_bps=_bps_ratio(abs(exit_px - open_snap.entry_px), open_snap.entry_px),
        risk_usd=_round(risk_usd, _ROUND_USD),
        r_multiple=(_round(net / risk_usd, _ROUND_BPS)
                    if risk_usd is not None and risk_usd > 0 else None),
        planned_rr=_planned_rr(open_snap), stop_atr_mult=_stop_atr_mult(open_snap),
        exit_source=_text(ctx.exit_source) or "unknown",
        est_edge_bps=(est.est_edge_bps if est else None),
        est_cost_bps=(est.est_cost_bps if est else None),
        edge_source=(est.edge_source if est else None),
        cost_confidence=(est.cost_confidence if est else None),
        decision_confidence=(est.decision_confidence if est else None),
        wake_kind=(est.wake_kind if est else None),
        edge_error_bps=_diff(realized_gross_bps, est.est_edge_bps if est else None),
        cost_error_bps=_diff(realized_cost_bps, est.est_cost_bps if est else None))


# 结算键 → wire 键（camelCase）。**顺序即 wire 顺序**，「算不出即省略」（约束 6）。
_WIRE_FIELDS: tuple[tuple[str, str], ...] = (
    ("seq", "tripSeq"), ("hold_seconds", "holdSeconds"), ("notional_usd", "notionalUsd"),
    ("move_bps", "moveBps"), ("realized_net_bps", "realizedNetBps"),
    ("realized_gross_bps", "realizedGrossBps"), ("realized_cost_bps", "realizedCostBps"),
    ("fee_usd", "roundTripFeeUsd"), ("r_multiple", "rMultiple"), ("planned_rr", "plannedRr"),
    ("stop_atr_mult", "stopAtrMult"), ("est_edge_bps", "estEdgeBps"),
    ("est_cost_bps", "estCostBps"), ("edge_source", "edgeSource"),
    ("cost_confidence", "costConfidence"), ("decision_confidence", "decisionConfidence"),
    ("wake_kind", "wakeKind"),
)


def trip_meta_wire(trip: SettledTrip | None) -> dict:
    """`explain.meta` 的加法键（**缺失即省略**，绝不写 `None`）。`exitSource` 由会话侧写，本模块不写。"""
    if trip is None:
        return {}
    out: dict = {}
    for attr, key in _WIRE_FIELDS:
        value = getattr(trip, attr, None)
        if value is not None:
            out[key] = value
    return out


def _planned_rr(snap: OpenSnapshot) -> float | None:
    """计划盈亏比 `|take − entry| / |entry − stop|`；任一缺席 / 分母为 0 ⇒ None。"""
    if snap.stop_px is None or snap.take_px is None:
        return None
    stop_distance = abs(snap.entry_px - snap.stop_px)
    if stop_distance <= 0:
        return None
    return _round(abs(snap.take_px - snap.entry_px) / stop_distance, _ROUND_BPS)


def _stop_atr_mult(snap: OpenSnapshot) -> float | None:
    """计划止损距离相当于开仓那一刻 ATR 的几倍；ATR / 止损任一不可用 ⇒ None（约束 4）。"""
    if snap.stop_px is None or snap.atr_px is None or snap.atr_px <= 0:
        return None
    distance = abs(snap.entry_px - snap.stop_px)
    return _round(distance / snap.atr_px, _ROUND_BPS) if distance > 0 else None


def _leg_fee(trade: dict) -> tuple[float, bool]:
    """一条腿的费率与「这条腿到底记没记费率」（约束 2b）。

    键缺席 / `None` / 非有限 ⇒ `(0.0, False)`；键在且是有限数（含 `0.0`）⇒ `(|fee|, True)`。
    **`0.0` 与「没记」在数据上不可区分正是 D-9 的根**，故这里必须靠「键在不在」分辨。
    """
    if "fee" not in trade:
        return 0.0, False
    value = _finite(trade.get("fee"))
    return (0.0, False) if value is None else (abs(value), True)


def _bps(value: float | None, notional: float | None) -> float | None:
    """`value / notional × 1e4`；分母缺席 / ≤0 ⇒ None（**绝不填 0**，约束 2）。"""
    if value is None or notional is None or notional <= 0:
        return None
    return _round(float(value) / notional * 1e4, _ROUND_BPS)


def _bps_ratio(numerator: float, denominator: float) -> float | None:
    """纯价格比值的 bps（`move_bps` 在 `ct_val` 未知时的退化路径：位移比例与合约乘数无关）。"""
    if denominator <= 0:
        return None
    return _round(numerator / denominator * 1e4, _ROUND_BPS)


def _diff(realized: float | None, estimate: float | None) -> float | None:
    """`已实现 − 估计`；任一缺席 ⇒ None（缺估计的往返不进校准分母，而不是按 0 计入）。"""
    if realized is None or estimate is None:
        return None
    return _round(realized - estimate, _ROUND_BPS)


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(float(value), digits)


def _positive(value) -> float | None:
    out = _finite(value)
    return out if out is not None and out > 0 else None


def _text(value) -> str | None:
    if value is None:
        return None
    out = str(value).strip()
    return out or None


def _finite(value) -> float | None:
    """有限数归一：非有限 / 不可转 ⇒ `None`（绝不把 NaN 带进任何判定）。"""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None
