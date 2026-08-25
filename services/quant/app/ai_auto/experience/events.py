"""经验事件的装配（ai-trade-experience-context-graph §4.3）—— 纯函数、零 IO、绝不抛。

三条口径纪律，逐条都有过前科：

1. **语境冻结在开仓那一刻**。出场时体制可能已经变了；用出场语境去标注一笔入场决策的教训是错的，
   而且不会报错——它只会让「高波动追突破亏钱」这条经验挂到「震荡」那一格上。
2. **结果补齐必须读 `SettledTrip` 本体，不能只读 `explain.meta`**（评审 P0-3）。`trip_meta_wire`
   只发 17 个键（`trip_ledger.py` 的 `_WIRE_FIELDS` 实读），**不含** `fee_known` / `side` /
   `entry_ts` / `edge_error_bps`；而 I-7 的口径闸靠 `fee_known`、事件表的列要 `side` 与 `entry_ts`、
   「估计偏差型」模板要 `edge_error_bps`。只读 meta 的实现会让这三处**静默**失效：口径闸空转、
   入场时刻只能瞎填、一个模板永不触发，全程零报错。
   本层只从 meta 读两个键：`exitSource`（沿用「只读不判、绝不自己调 `classify_live`」的成文纪律）
   与 `wallTs`。
3. **双时钟不混用**。`*_ts` 是秒级 bar 时钟（历史查询用），`*_wall_ts` 是墙钟（时间衰减用）。
   `SettledTrip` **不带任何墙钟字段**（`OpenSnapshot.wall_ts` 在 `settle()` 里被消费掉了），
   这就是入场墙钟必须由 `note_open` 当场冻结的原因。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from ..trip_ledger import SettledTrip, trip_meta_wire
from . import taxonomy as tax


@dataclass(frozen=True)
class OpenContext:
    """开仓成交那一刻冻结的语境断面（按 `(side, seq)` 存槽，与 `OpenSnapshot` 同一 epoch 身份）。"""

    seq: int
    side: str
    signature: dict[str, str]          # 九轴（`taxonomy.OPEN_AXES`）
    wall_ts: int                       # 入场墙钟（`SettledTrip` 不带墙钟，故必须在此冻结）


@dataclass(frozen=True)
class EventIdentity:
    """事件的溯源身份（聚合形参，满足 CLAUDE.md「形参 ≤5」）。"""

    group_id: str
    run_id: int
    inst_id: str
    bar: str
    origin: str = "live"


@dataclass(frozen=True)
class ExperienceEvent:
    """一条原子事实（`ai_experience_events` 的一行）。append-only，绝不改写。"""

    identity: EventIdentity
    seq: int
    side: str
    entry_ts: int
    exit_ts: int
    entry_wall_ts: int
    exit_wall_ts: int
    context: dict[str, str]
    outcome: dict
    cost_basis: str
    fee_known: bool
    hold_seconds: float
    exit_source: str
    net_bps: float | None = None
    gross_bps: float | None = None
    cost_bps: float | None = None
    edge_error_bps: float | None = None
    created_ts: int = 0

    def row(self) -> dict:
        """落库行（列名即 `AiExperienceEvent` 的列，数据层直接 `**row` 构造）。"""
        return {
            "group_id": self.identity.group_id, "run_id": self.identity.run_id,
            "inst_id": self.identity.inst_id, "bar": self.identity.bar,
            "origin": self.identity.origin, "seq": self.seq, "side": self.side,
            "entry_ts": self.entry_ts, "exit_ts": self.exit_ts,
            "entry_wall_ts": self.entry_wall_ts, "exit_wall_ts": self.exit_wall_ts,
            "context": dict(self.context), "outcome": dict(self.outcome),
            "cost_basis": self.cost_basis, "fee_known": self.fee_known,
            "net_bps": self.net_bps, "gross_bps": self.gross_bps, "cost_bps": self.cost_bps,
            "edge_error_bps": self.edge_error_bps, "hold_seconds": self.hold_seconds,
            "exit_source": self.exit_source, "created_ts": self.created_ts,
        }


@dataclass
class ContextFacts:
    """`freeze_open_context` 的入参聚合（会话在开仓成交那一刻能直接读到的事实）。"""

    inst_id: str
    bar: str
    side: str
    market_context: dict | None = None      # `build_market_context` 产物（regime/volatility/event）
    wake_kind: str | None = None
    horizon_profile_id: str | None = None
    confidence: float | None = None
    extra: dict = field(default_factory=dict)


def freeze_open_context(facts: ContextFacts, *, seq: int, now_wall: float) -> OpenContext:
    """开仓成交 → 九轴语境签名（越界取值一律折成 `unknown`，**绝不丢事件**）。

    Args:
        facts: 开仓瞬刻可读的事实。
        seq: 会话级持仓序号（epoch 身份的一半）。
        now_wall: 墙钟秒（入场墙钟的唯一采集点）。

    Returns:
        冻结的 `OpenContext`；此后该持仓周期内不再变化。
    """
    market = facts.market_context or {}
    signature = {
        tax.AXIS_SYMBOL: tax.fold(tax.AXIS_SYMBOL, facts.inst_id),
        tax.AXIS_BAR: tax.fold(tax.AXIS_BAR, facts.bar),
        tax.AXIS_REGIME: tax.fold(tax.AXIS_REGIME, market.get("primary")),
        tax.AXIS_VOLATILITY: tax.fold(tax.AXIS_VOLATILITY, market.get("volatility")),
        tax.AXIS_EVENT: tax.fold(tax.AXIS_EVENT, market.get("event")),
        tax.AXIS_SIDE: tax.fold(tax.AXIS_SIDE, facts.side),
        tax.AXIS_WAKE: tax.fold(tax.AXIS_WAKE, facts.wake_kind),
        tax.AXIS_HORIZON: tax.fold(tax.AXIS_HORIZON, facts.horizon_profile_id or "none"),
        tax.AXIS_CONFIDENCE: tax.confidence_band(facts.confidence),
    }
    return OpenContext(seq=int(seq), side=signature[tax.AXIS_SIDE], signature=signature,
                       wall_ts=int(now_wall or time.time()))


def build_event(opened: OpenContext, trip: SettledTrip, meta: dict,
                identity: EventIdentity) -> ExperienceEvent | None:
    """把「冻结语境 + 已结算往返 + 退出来源」组装成一条事件；缺任一必需输入 ⇒ `None`。

    `cost_basis` 由调用方经 `meta["_costBasis"]` 传入（会话的 `ai_trade_net_pnl_enabled`，与
    `manager` 写进 run metrics 的 `costBasis` 同源同值），缺席按 `gross` 处理——**宁可把一条
    事件排除在 bps 统计外，也不能把毛额当净额混进同一张表**（I-7）。

    Args:
        opened: 开仓瞬刻的九轴语境（`freeze_open_context` 产物）。
        trip: `SettledTrip` 本体（**不是** `trip_meta_wire`，见模块 docstring 第 2 条）。
        meta: `trade["explain"]["meta"]`；本层只读 `exitSource` / `wallTs` / `_costBasis`。
        identity: 溯源身份。

    Returns:
        组装好的事件；`trip` 为空或语境缺席 ⇒ `None`（调用方据此计 `harvestGap`，绝不编一条事件）。
    """
    if trip is None or opened is None:
        return None
    exit_source = tax.fold(tax.AXIS_EXIT, meta.get("exitSource") or trip.exit_source)
    context = dict(opened.signature)
    context[tax.AXIS_EXIT] = exit_source
    exit_wall = _int(meta.get("wallTs")) or int(time.time())
    return ExperienceEvent(
        identity=identity, seq=int(trip.seq), side=tax.fold(tax.AXIS_SIDE, trip.side),
        entry_ts=int(trip.entry_ts or 0), exit_ts=int(trip.exit_ts or 0),
        entry_wall_ts=int(opened.wall_ts or 0), exit_wall_ts=exit_wall,
        context=context, outcome=_outcome_wire(trip),
        cost_basis=("net" if meta.get("_costBasis") == "net" else "gross"),
        fee_known=bool(trip.fee_known),
        hold_seconds=float(trip.hold_seconds or 0.0), exit_source=exit_source,
        net_bps=_finite(trip.realized_net_bps), gross_bps=_finite(trip.realized_gross_bps),
        cost_bps=_finite(trip.realized_cost_bps), edge_error_bps=_finite(trip.edge_error_bps),
        created_ts=int(time.time()))


def _outcome_wire(trip: SettledTrip) -> dict:
    """`outcome_json` 的内容：`trip_meta_wire` 的 17 键 + 四个它**不发**但本特性要用的键。

    这四个键（`side` / `entryTs` / `exitTs` / `feeKnown`）不是重复——`trip_meta_wire` 的省略是
    对 `explain.meta` 体积的克制，而本表是经验资产的真源，缺了它们详情抽屉的溯源表就没有方向、
    没有入场时刻，口径闸也无从判定（评审 P0-3）。
    """
    wire = dict(trip_meta_wire(trip))
    wire.update({"side": trip.side, "entryTs": trip.entry_ts, "exitTs": trip.exit_ts,
                 "feeKnown": trip.fee_known})
    for attr, key in (("gross_pnl_usd", "grossPnlUsd"), ("net_pnl_usd", "netPnlUsd"),
                      ("edge_error_bps", "edgeErrorBps"), ("cost_error_bps", "costErrorBps")):
        value = _finite(getattr(trip, attr, None))
        if value is not None:
            wire[key] = value
    return wire


def _finite(value) -> float | None:
    """有限浮点或 `None`（**绝不填 0**：0 会被聚合读成「成本为零」）。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0
