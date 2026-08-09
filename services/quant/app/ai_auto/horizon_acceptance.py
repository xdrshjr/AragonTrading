"""周期验收卡纯核心（ai-trade-horizon-acceptance-loop 轴 C · §2.4）。

调查原文最后一段点名要求的五个验收指标（平均持仓时间 / 每日换手次数 / 手续费占毛利润 /
资金费后净收益 / 不同退出来源）在这里被汇成**一张五行判据卡**。回测与实盘共用同一个 builder、
同一个 wire 形状，分组多标的按**原始计数器可加合并**后再判定一次。

三条必须先读的口径：

1. **样本不足永远不是 `pass`。** `insufficient` 的语义是「什么都还不知道」，它在 UI 上是灰色
   而不是黄色（黄色会被读成「快通过了」）。这是 L1 放行门槛（「未通过样本外净额回测前不得放大
   实盘资金」）唯一的技术保证——一个样本不足就变绿的卡片会直接把这条纪律作废。
2. **原始计数器可精确合并，比例不可平均。** 费/毛、命中率、换手率按成员平均会得到一个谁都不
   对应的数；故合并的是计数器，判定只发生一次（[P1-2]）。
3. **没有目标区间就没有门槛。** `HorizonRule.target_hold_min_s` 默认 `0.0` 且 `custom` 全默认
   是文档化的安全退出口——此时第 1、3 行判 `insufficient`（绝不 `pass`、绝不除零），第 2 行
   本来就没有靶子（[P0-2]）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .acceptance_text import (
    ROW_LABELS,
    avg_hold_note,
    build_diagnostics,
    fee_to_gross_note,
    format_target,
    format_value,
    net_after_funding_note,
    sample_note,
    turnover_note,
    within_target_note,
)
from .exit_attribution import ExitLedger, exit_counters
from .funding_ledger import merge_funding_wires
from .turnover_discipline import derive_turnover_per_day

VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
VERDICT_INSUFFICIENT = "insufficient"

_SECONDS_PER_DAY = 86400.0
# `basis` 的强弱序（合并取最弱的那一档：任一成员不可得 ⇒ 整组不可得，绝不让一个取到数的成员
# 替另一个没取到的成员背书）。
_BASIS_RANK = {"settled+accrued": 2, "accrued": 1, "unavailable": 0}
_BASIS_BY_RANK = {2: "settled+accrued", 1: "accrued", 0: "unavailable"}


@dataclass(frozen=True)
class AcceptanceInput:
    """验收判定的**原始计数器**（可精确合并；一切比例都由它现算，绝不合并比例）。"""

    round_trips: int = 0
    hold_seconds_sum: float = 0.0
    hold_samples: int = 0
    within_target_samples: int = 0
    # 自指披露（turnover-budget §2.2.6）：本会话是否武装了 G5 换手预算闸、它实际拦下多少次。
    # 一旦 G5 在运行期执行 `turnoverPerDay <= 阈`，第 3 行就从「独立观测」退化为「执行结果」、
    # 近乎恒真——处置办法不是把闸关掉，而是在 note 里**说出来**。
    turnover_gate_armed: bool = False
    turnover_budget_vetoes: int = 0
    # [P1-8] 其中被区间 / 会话边界截断的样本数（退出来源 ∈ TRUNCATING_SOURCES）。
    # **不从样本里剔除**——剔除会造幸存者偏差（专挑走完全程的持仓算平均持仓时间）；
    # 只在第 1 行的 note 里如实披露「实际持仓时长被低估」。
    truncated_samples: int = 0
    # [P1-1] 成本四项一律是**全程累计**口径，不是 `AttributionLedger` 那个 200 容量环形窗口。
    gross_pnl_usd: float = 0.0
    fee_usd: float = 0.0
    net_pnl_usd: float = 0.0
    abs_gross_pnl_usd: float = 0.0
    funding_cost_usd: float | None = None
    funding_basis: str = "unavailable"
    # [P1-3] 三个世界的取值见 §2.4.1；本模块只做 `max(1.0, …)` 兜底。
    wall_seconds: float = 0.0
    exit_counts: dict[str, int] = field(default_factory=dict)
    exit_net_usd: dict[str, float] = field(default_factory=dict)
    exit_hold_sum: dict[str, float] = field(default_factory=dict)
    # 时长样本数独立成表：由 `exit_hold_sum` 的键集反推会把「有笔数但无时长」的样本算进分母。
    exit_hold_samples: dict[str, int] = field(default_factory=dict)
    exit_wins: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class AcceptanceThresholds:
    """五行判据的阈值；`None` = 该行没有门槛 ⇒ 判 `insufficient`（[P0-2]）。"""

    sample_floor: int = 20
    min_avg_hold_s: float | None = None
    min_within_target_rate: float | None = None
    max_turnover_per_day: float | None = None
    derived_turnover_per_day: float | None = None   # 未被硬上限夹之前的派生值（供 note 自认）
    max_fee_to_gross: float = 0.35


def build_input(*, horizon_parts: dict | None, lifetime_cost: dict,
                exit_stats: dict | None, funding_stats: dict | None,
                wall_seconds: float) -> AcceptanceInput | None:
    """把四个来源的**原始计数器**装配成一份可合并的验收输入；零数据 ⇒ `None`。

    Args:
        horizon_parts: `HorizonRuntime.acceptance_input_parts()` 的原始计数器（未装配 ⇒ `None`）。
            **绝不吃 `stats()` 的 round 产物**——`avgHoldSeconds` 只保留 3 位小数、
            `withinTargetRate` 只保留 4 位，反乘回去会引入舍入误差，多成员合并后误差累积，
            与「可精确合并」自相矛盾（[P1-2]）。
        lifetime_cost: `AttributionLedger.lifetime_cost()` 的全程累计口径（[P1-1]）。
        exit_stats: `exit_attribution.exit_counters()` 产物（未记录任何退出 ⇒ 计数全空）。
        funding_stats: `FundingLedger.stats()` 产物（未启用 ⇒ `None` ⇒ `basis="unavailable"`）。
        wall_seconds: 墙钟口径的分母，三世界取值见 §2.4.1。

    Returns:
        `AcceptanceInput`；完全没有任何样本（零往返、零退出）⇒ `None`。
    """
    parts = horizon_parts or {}
    exits = exit_stats or {}
    counts = dict(exits.get("counts") or {})
    trips = int(parts.get("roundTrips") or 0)
    if trips <= 0 and not counts:
        return None
    cost = lifetime_cost or {}
    return AcceptanceInput(
        round_trips=trips,
        hold_seconds_sum=_num(parts.get("holdSecondsSum")),
        hold_samples=int(parts.get("holdSamples") or 0),
        within_target_samples=int(parts.get("withinTargetSamples") or 0),
        turnover_gate_armed=bool(parts.get("turnoverGateArmed") or False),
        turnover_budget_vetoes=int(parts.get("turnoverBudgetVetoes") or 0),
        truncated_samples=int(exits.get("truncated") or 0),
        gross_pnl_usd=_num(cost.get("grossPnlUsd")),
        fee_usd=_num(cost.get("feeUsd")),
        net_pnl_usd=_num(cost.get("netPnlUsd")),
        abs_gross_pnl_usd=_num(cost.get("absGrossPnlUsd")),
        funding_cost_usd=_optional(( funding_stats or {}).get("fundingCostUsd")),
        funding_basis=str((funding_stats or {}).get("basis") or "unavailable"),
        wall_seconds=_num(wall_seconds),
        exit_counts=counts,
        exit_net_usd=dict(exits.get("netUsd") or {}),
        exit_hold_sum=dict(exits.get("holdSum") or {}),
        exit_hold_samples=dict(exits.get("holdSamples") or {}),
        exit_wins=dict(exits.get("wins") or {}))


def merge_inputs(items: list[AcceptanceInput]) -> AcceptanceInput | None:
    """多成员（多标的分组）合并；空表 ⇒ `None`。

    **`wall_seconds` 取 `max` 而不是求和**——成员是并发跑的，求和会把一个 2 成员分组的墙钟翻倍，
    进而把每日换手次数腰斩（一个恰好总是通过的门槛）。其余标量相加、字典逐键相加。
    `funding_basis` 取**最弱**的那一档；`funding_cost_usd` 只在**全部**成员都有值时相加，
    任一成员不可得即整体 `None`（半份数据不得冒充整组结论）。
    """
    rows = [i for i in items if i is not None]
    if not rows:
        return None
    fundings = [r.funding_cost_usd for r in rows]
    total_funding = (None if any(v is None for v in fundings)
                     else sum(float(v) for v in fundings))
    rank = min(_BASIS_RANK.get(r.funding_basis, 0) for r in rows)
    return AcceptanceInput(
        round_trips=sum(r.round_trips for r in rows),
        hold_seconds_sum=sum(r.hold_seconds_sum for r in rows),
        hold_samples=sum(r.hold_samples for r in rows),
        within_target_samples=sum(r.within_target_samples for r in rows),
        # `armed` 取 `or`、`vetoes` 相加：任一成员武装了闸，整组的第 3 行就已不是独立观测。
        turnover_gate_armed=any(r.turnover_gate_armed for r in rows),
        turnover_budget_vetoes=sum(r.turnover_budget_vetoes for r in rows),
        truncated_samples=sum(r.truncated_samples for r in rows),
        gross_pnl_usd=sum(r.gross_pnl_usd for r in rows),
        fee_usd=sum(r.fee_usd for r in rows),
        net_pnl_usd=sum(r.net_pnl_usd for r in rows),
        abs_gross_pnl_usd=sum(r.abs_gross_pnl_usd for r in rows),
        funding_cost_usd=total_funding,
        funding_basis=_BASIS_BY_RANK[rank],
        wall_seconds=max(r.wall_seconds for r in rows),
        exit_counts=_merge_int(rows, "exit_counts"),
        exit_net_usd=_merge_float(rows, "exit_net_usd"),
        exit_hold_sum=_merge_float(rows, "exit_hold_sum"),
        exit_hold_samples=_merge_int(rows, "exit_hold_samples"),
        exit_wins=_merge_int(rows, "exit_wins"))


def input_wire(inp: AcceptanceInput | None) -> dict | None:
    """原始计数器的 camelCase wire（落 `aiStats.acceptanceInput`，分组历史合并的唯一输入）。"""
    if inp is None:
        return None
    return {"roundTrips": inp.round_trips, "holdSecondsSum": inp.hold_seconds_sum,
            "holdSamples": inp.hold_samples,
            "withinTargetSamples": inp.within_target_samples,
            "turnoverGateArmed": inp.turnover_gate_armed,
            "turnoverBudgetVetoes": inp.turnover_budget_vetoes,
            "truncatedSamples": inp.truncated_samples,
            "grossPnlUsd": inp.gross_pnl_usd, "feeUsd": inp.fee_usd,
            "netPnlUsd": inp.net_pnl_usd, "absGrossPnlUsd": inp.abs_gross_pnl_usd,
            "fundingCostUsd": inp.funding_cost_usd, "fundingBasis": inp.funding_basis,
            "wallSeconds": inp.wall_seconds, "exitCounts": dict(inp.exit_counts),
            "exitNetUsd": dict(inp.exit_net_usd), "exitHoldSum": dict(inp.exit_hold_sum),
            "exitHoldSamples": dict(inp.exit_hold_samples),
            "exitWins": dict(inp.exit_wins)}


def input_from_wire(wire: dict | None) -> AcceptanceInput | None:
    """`input_wire` 的逆变换（历史·分组详情在**后端**合并 N 个 run 时用，[P1-9]）。"""
    if not isinstance(wire, dict):
        return None
    return AcceptanceInput(
        round_trips=int(wire.get("roundTrips") or 0),
        hold_seconds_sum=_num(wire.get("holdSecondsSum")),
        hold_samples=int(wire.get("holdSamples") or 0),
        within_target_samples=int(wire.get("withinTargetSamples") or 0),
        turnover_gate_armed=bool(wire.get("turnoverGateArmed") or False),
        turnover_budget_vetoes=int(wire.get("turnoverBudgetVetoes") or 0),
        truncated_samples=int(wire.get("truncatedSamples") or 0),
        gross_pnl_usd=_num(wire.get("grossPnlUsd")), fee_usd=_num(wire.get("feeUsd")),
        net_pnl_usd=_num(wire.get("netPnlUsd")),
        abs_gross_pnl_usd=_num(wire.get("absGrossPnlUsd")),
        funding_cost_usd=_optional(wire.get("fundingCostUsd")),
        funding_basis=str(wire.get("fundingBasis") or "unavailable"),
        wall_seconds=_num(wire.get("wallSeconds")),
        exit_counts=dict(wire.get("exitCounts") or {}),
        exit_net_usd=dict(wire.get("exitNetUsd") or {}),
        exit_hold_sum=dict(wire.get("exitHoldSum") or {}),
        exit_hold_samples=dict(wire.get("exitHoldSamples") or {}),
        exit_wins=dict(wire.get("exitWins") or {}))


def counters_from_input(inp: AcceptanceInput) -> dict:
    """`AcceptanceInput` → `ExitLedger.from_counters` 的入参（合并后重建退出分布 wire 用）。"""
    return {"counts": dict(inp.exit_counts), "netUsd": dict(inp.exit_net_usd),
            "holdSum": dict(inp.exit_hold_sum),
            "holdSamples": dict(inp.exit_hold_samples), "wins": dict(inp.exit_wins),
            "truncated": inp.truncated_samples}


def resolve_thresholds(settings, rule) -> AcceptanceThresholds:
    """settings + 周期契约 → 阈值（唯一解析点）。

    第 1、3 行的阈值**全部从周期契约派生**（换模式即换阈值，不引入新魔数）；`target_hold_min_s`
    / `target_hold_max_s` ≤ 0 ⇒ 对应阈值置 `None`，由 `build_acceptance` 翻成该行
    `insufficient`——**绝不在此处除零、也绝不回落到任何默认目标区间**（[P0-2]）。

    [P1-4] 第 3 行的两个阈值（已夹 / 未夹）**一次**取自 `turnover_discipline.derive_turnover_per_day`
    ——它同时也是运行期 G5 令牌桶速率的唯一读点（turnover-budget §2.2.1）。本模块内联
    `86400 / lo` 会造出第二处口径：用户在弹窗读到的门槛、运行期真正执行的预算、事后验收的判据
    从此可以各自漂移，而这三者在用户眼里本就该是同一个数。
    """
    floor = int(getattr(settings, "ai_trade_acceptance_min_round_trips", 20))
    max_fee = float(getattr(settings, "ai_trade_acceptance_max_fee_to_gross", 0.35))
    within = float(getattr(settings, "ai_trade_acceptance_min_within_target_rate", 0.6))
    lo = float(getattr(rule, "target_hold_min_s", 0.0) or 0.0)
    hi = float(getattr(rule, "target_hold_max_s", 0.0) or 0.0)
    capped, derived = derive_turnover_per_day(lo)
    return AcceptanceThresholds(
        sample_floor=floor,
        min_avg_hold_s=lo if lo > 0 else None,
        min_within_target_rate=within if hi > 0 else None,
        max_turnover_per_day=capped,
        derived_turnover_per_day=derived,
        max_fee_to_gross=max_fee)


def build_acceptance(inp: AcceptanceInput | None, rule, thresholds, *,
                     exits: dict | None, funding: dict | None) -> dict | None:
    """§2.4.4 形状的验收卡；未装配周期契约 / 无输入 ⇒ `None`（整键缺席）。

    `exits` / `funding` 由调用方传入并**原样引用**进 wire 的 `exitMix` / `funding`，本函数
    不重新装配它们——两处各算一份就是本仓库反复出事的那类分叉（[P0-4] / W-9）。
    """
    if inp is None or rule is None:
        return None
    below_floor = inp.round_trips < int(thresholds.sample_floor)
    rows = [_row_avg_hold(inp, thresholds), _row_within_target(inp, thresholds),
            _row_turnover(inp, thresholds), _row_fee_to_gross(inp, thresholds),
            _row_net_after_funding(inp)]
    if below_floor:
        # A-2：样本不足时**没有任何一行是绿色**——值照常下发，结论一律「还不知道」。
        rows = [{**row, "verdict": VERDICT_INSUFFICIENT} for row in rows]
    verdicts = [row["verdict"] for row in rows]
    return {
        "profileId": getattr(rule, "profile_id", ""),
        "title": getattr(rule, "title", "") or getattr(rule, "profile_id", ""),
        "verdict": _overall(verdicts, below_floor),
        "sampleFloor": int(thresholds.sample_floor),
        "roundTrips": inp.round_trips,
        "truncatedSamples": inp.truncated_samples,
        "sampleNote": sample_note(inp.round_trips, int(thresholds.sample_floor)),
        "rows": rows,
        # 顶层同名键的**同一个对象引用**；前端一律读顶层，此处仅供 diagnostics / tooltip 就近取用。
        "exitMix": exits,
        "funding": funding,
        "diagnostics": _diagnostics(inp, rows, thresholds),
    }


def build_bundle(horizon, cost, *, settings, wall_seconds: float) -> dict:
    """验收闭环四键的**唯一装配点**（运行期 status / 落库 / 分组合并 / 回测共用）。

    `exits` / `funding` 既是**顶层键**、又作为 `acceptance.exitMix` / `acceptance.funding` 的
    **同一个对象引用**出现（[P0-4] / D-11）：顶层保证「关掉周期模式的对照臂」照样可见（它是 L1
    的必要臂），镜像保证 diagnostics 与 tooltip 能就近取数。**绝不二次装配**——两处各算一份就是
    本仓库反复出事的那类分叉（W-9 断言两处序列化后逐键相等）。

    Args:
        horizon: `HorizonRuntime`（只读 `rule` / `acceptance_input_parts()` / `exits`）。
        cost: `CostRuntime`（只读 `lifetime_cost()` / `funding_stats()`）。
        settings: 运行配置（阈值与总闸）。
        wall_seconds: 墙钟口径分母，三世界取值见 §2.4.1。

    Returns:
        `{"acceptance", "exits", "funding", "acceptanceInput"}`——**全部 JSON 安全**，
        故可原样 spread 进 status payload 与 `aiStats`。
    """
    exits_wire = horizon.exit_stats()
    funding_wire = cost.funding_stats()
    inp = build_input(horizon_parts=horizon.acceptance_input_parts(),
                      lifetime_cost=cost.lifetime_cost(),
                      exit_stats=exit_counters(horizon.exits),
                      funding_stats=funding_wire, wall_seconds=wall_seconds)
    return bundle_from_input(inp, rule=horizon.rule, settings=settings,
                             exits=exits_wire, funding=funding_wire)


def bundle_from_input(inp: AcceptanceInput | None, *, rule, settings,
                      exits: dict | None, funding: dict | None) -> dict:
    """四键装配（运行期 / 分组 / 历史 / 回测四处共用；总闸关 ⇒ 卡片为 `None`）。"""
    card = None
    if getattr(settings, "ai_trade_acceptance_enabled", True):
        card = build_acceptance(inp, rule, resolve_thresholds(settings, rule),
                                exits=exits, funding=funding)
    return {"acceptance": card, "exits": exits, "funding": funding,
            "acceptanceInput": input_wire(inp)}


def merge_bundle(inputs: list[AcceptanceInput], *, rule, settings,
                 fundings: list[dict | None]) -> dict:
    """多成员 / 多 run 的合并装配（分组运行期与历史详情共用，[P1-9]）。

    合并的是**原始计数器**而不是已判定的卡——比例类指标（费/毛、命中率、换手率）不可平均，
    按成员平均会得到一个谁都不对应的数；判定只发生一次。
    """
    merged = merge_inputs(inputs)
    if merged is None:
        return {"acceptance": None, "exits": None, "funding": None,
                "acceptanceInput": None}
    exits = ExitLedger.from_counters(counters_from_input(merged)).stats()
    return bundle_from_input(merged, rule=rule, settings=settings, exits=exits,
                             funding=merge_funding_wires(fundings))


def ai_stats_keys(bundle: dict) -> dict:
    """`bundle` → `aiStats` 的四个加法键（落库口径的键名与 status 不同，故在此单点改名）。"""
    return {"horizonAcceptance": bundle.get("acceptance"),
            "exits": bundle.get("exits"), "funding": bundle.get("funding"),
            "acceptanceInput": bundle.get("acceptanceInput")}


def _overall(verdicts: list[str], below_floor: bool) -> str:
    if below_floor:
        return VERDICT_INSUFFICIENT
    if VERDICT_FAIL in verdicts:
        return VERDICT_FAIL
    if VERDICT_INSUFFICIENT in verdicts:
        return VERDICT_INSUFFICIENT
    return VERDICT_PASS


def _row(key: str, unit: str, value, threshold, verdict: str, note: str | None,
         extra: dict | None = None) -> dict:
    out = {"key": key, "label": ROW_LABELS[key], "verdict": verdict, "value": value,
           "unit": unit, "display": format_value(unit, value),
           "target": format_target(key, threshold), "note": note}
    if extra:
        out.update(extra)
    return out


def _row_avg_hold(inp: AcceptanceInput, th: AcceptanceThresholds) -> dict:
    value = (inp.hold_seconds_sum / inp.hold_samples) if inp.hold_samples else None
    verdict = _compare_min(value, th.min_avg_hold_s)
    return _row("avgHold", "seconds", value, th.min_avg_hold_s, verdict,
                avg_hold_note(inp.truncated_samples, th.min_avg_hold_s is not None))


def _row_within_target(inp: AcceptanceInput, th: AcceptanceThresholds) -> dict:
    value = (inp.within_target_samples / inp.hold_samples) if inp.hold_samples else None
    verdict = _compare_min(value, th.min_within_target_rate)
    return _row("withinTargetRate", "ratio", value, th.min_within_target_rate, verdict,
                within_target_note(th.min_within_target_rate is not None))


def _row_turnover(inp: AcceptanceInput, th: AcceptanceThresholds) -> dict:
    days = inp.wall_seconds / _SECONDS_PER_DAY
    value = (inp.round_trips / days) if days > 0 else None
    in_pos_days = inp.hold_seconds_sum / _SECONDS_PER_DAY
    in_position = (inp.hold_samples / in_pos_days) if in_pos_days > 0 else None
    verdict = _compare_max(value, th.max_turnover_per_day)
    return _row("turnoverPerDay", "count", value, th.max_turnover_per_day, verdict,
                turnover_note(in_position, th.max_turnover_per_day,
                              th.derived_turnover_per_day,
                              gate_armed=inp.turnover_gate_armed,
                              gate_vetoes=inp.turnover_budget_vetoes),
                {"inPositionValue": (None if in_position is None
                                     else round(in_position, 4)),
                 # 自指披露的两个原始读数（前端 tooltip 与运维 runbook 共用；§2.2.6）。
                 "gateArmed": inp.turnover_gate_armed,
                 "gateVetoes": inp.turnover_budget_vetoes})


def _row_fee_to_gross(inp: AcceptanceInput, th: AcceptanceThresholds) -> dict:
    value = (inp.fee_usd / inp.abs_gross_pnl_usd) if inp.abs_gross_pnl_usd > 0 else None
    verdict = _compare_max(value, th.max_fee_to_gross)
    return _row("feeToGross", "ratio", value, th.max_fee_to_gross, verdict,
                fee_to_gross_note(inp.abs_gross_pnl_usd))


def _row_net_after_funding(inp: AcceptanceInput) -> dict:
    """第 5 行是硬判据；资金费不可得 ⇒ `insufficient`（**绝不**当 0 减）。"""
    if inp.funding_cost_usd is None:
        return _row("netAfterFunding", "usd", None, None, VERDICT_INSUFFICIENT,
                    net_after_funding_note(inp.net_pnl_usd, None, inp.funding_basis),
                    {"basis": inp.funding_basis})
    value = inp.net_pnl_usd - float(inp.funding_cost_usd)
    verdict = VERDICT_PASS if value > 0 else VERDICT_FAIL
    return _row("netAfterFunding", "usd", value, None, verdict,
                net_after_funding_note(inp.net_pnl_usd, float(inp.funding_cost_usd),
                                       inp.funding_basis),
                {"basis": inp.funding_basis})


def _compare_min(value: float | None, threshold: float | None) -> str:
    if value is None or threshold is None:
        return VERDICT_INSUFFICIENT
    return VERDICT_PASS if value >= threshold else VERDICT_FAIL


def _compare_max(value: float | None, threshold: float | None) -> str:
    if value is None or threshold is None:
        return VERDICT_INSUFFICIENT
    return VERDICT_PASS if value <= threshold else VERDICT_FAIL


def _diagnostics(inp: AcceptanceInput, rows: list[dict],
                 th: AcceptanceThresholds) -> list[str]:
    by_key = {row["key"]: row for row in rows}
    total = sum(inp.exit_counts.values())
    share = (inp.exit_counts.get("agent", 0) / total) if total else None
    within = by_key["withinTargetRate"]
    return build_diagnostics(
        agent_share=share, fee_to_gross=by_key["feeToGross"]["value"],
        fee_threshold=th.max_fee_to_gross,
        checks={"withinTargetPass": within["verdict"] == VERDICT_PASS,
                "netAfterFunding": by_key["netAfterFunding"]["value"],
                "fundingBasis": inp.funding_basis,
                "avgHoldSeconds": by_key["avgHold"]["value"]})


def _merge_int(rows: list[AcceptanceInput], attr: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        for key, value in getattr(row, attr).items():
            out[key] = out.get(key, 0) + int(value)
    return out


def _merge_float(rows: list[AcceptanceInput], attr: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        for key, value in getattr(row, attr).items():
            out[key] = out.get(key, 0.0) + float(value)
    return out


def _num(value) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if math.isfinite(out) else 0.0


def _optional(value) -> float | None:
    """`None` 与 `0.0` 是两个结论，故不可用 `_num` 兜底（F-9 守它）。"""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None
