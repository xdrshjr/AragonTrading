"""往返成本模型纯核心（ai-trade-cost-accounting-and-edge-gate §3.4）。

本模块是「一笔往返到底要付多少成本」的**唯一真源**：`C = feeOpenBps + feeCloseBps +
crossingLegs × (halfSpreadBps + impactBps) + fundingCarryBps`。纯函数、无 IO、无 LLM、无 broker，
实盘 `session` 与回测 `backtest` 共用同一份核心（镜像 `base_rules` / `signals.apply_consensus_gate`
的既有姿态）。

三条不可动摇的不变式（改本文件前先读）：
1. **决策轮内零网络 IO**。资金费率由调用方以「会话级内存快照」形式传入（`FundingSnapshot`），
   本模块绝不自己取数。
2. **取数失败绝不静默按 0 计**（§3.5）：`funding_rate is None` ⇒ `funding_carry_bps is None` +
   `confidence="degraded"`；而**有意关闭**资金费率建模时调用方传 `0.0` ⇒ `confidence` 保持 `"ok"`。
   「缺数据」与「有意不建模」是两件事，混为一谈会让门禁在断网时悄悄放松。
3. **资金费率方向感知且只取不利侧**：收费的一侧不给 edge 折扣（绝不因预期收费而放松门禁），
   付费的一侧全额计入。

与 01 号诊断文档的一处**显式泛化**（spec §3.4）：文档写的是固定 `2×` 半价差，本模块按开仓腿的
委托类型取 `crossing_legs ∈ {1, 2}`——maker 腿按定义是被动方、不吃半价差，固定 `2×` 会抹掉
maker-first 的一半收益来源，与 D-17 要解决的问题自相矛盾。`ord_type=="market"` 时本式退化为文档
原式，故是严格泛化而非改口径。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# 永续资金费率的默认结算周期（秒）：OKX 主流合约为 8h，取数缺失时的保守回落值。
DEFAULT_FUNDING_INTERVAL_S: float = 28800.0

# 成本读数的统一小数位（避免浮点尾巴污染 wire 与断言；不改变任何判定量级）。
_ROUND_BPS = 6

# 开仓腿吃价差的腿数：市价两腿都吃，限价开仓只有平仓腿吃。
_CROSSING_LEGS = {"limit": 1, "market": 2}


@dataclass(frozen=True)
class CostParams:
    """成本模型的世界常数（实盘一份、回测一份，两个世界必须能各自演进，§3.7）。"""

    maker_fee_bps: float
    taker_fee_bps: float
    half_spread_fallback_bps: float
    impact_bps: float
    expected_hold_bars: int


@dataclass(frozen=True)
class CostQuery:
    """一次往返成本估算的入参（全部为**显式**入参，使本模块保持纯函数）。"""

    ord_type: str                       # "market" | "limit"（开仓腿意图）
    side: str                           # "long" | "short"（资金费率收 / 付方向）
    bar_seconds: int
    half_spread_bps: float | None = None    # None ⇒ 回落 params.half_spread_fallback_bps
    funding_rate: float | None = None       # None ⇒ 缺席降级；0.0 ⇒ 有意不建模
    funding_interval_s: float = DEFAULT_FUNDING_INTERVAL_S
    # 结算周期是否**真的取自交易所**（OKX 由 `nextFundingTs − fundingTs` 得出真实值，会动态变为
    # 1/2/4/8 小时）。False ⇒ 上面那个数只是保守回落值，文案必须写「结算周期未知，按 8h 保守估算」
    # 而不能报出一个确定的周期（holding-horizon §2.7 的诚实约束）。
    funding_interval_known: bool = False


@dataclass(frozen=True)
class CostBreakdown:
    """逐项拆开的往返成本（R-5：拆开正是为了让「退化为两项简化式」只需改一个分支）。"""

    fee_open_bps: float
    fee_close_bps: float
    half_spread_bps: float
    impact_bps: float
    crossing_legs: int
    funding_carry_bps: float | None
    total_bps: float
    confidence: str                     # "ok" | "degraded"
    # 资金费的**期数与结算周期**（holding-horizon §2.7）：1–2 天持仓必须让模型看见「跨几个结算点」，
    # 而 OKX 的结算周期会动态变为 1/2/4/8 小时，故绝不能在文案里写死 8h。取不到 `intervalS` 时
    # `funding_interval_s is None` ⇒ 文案如实写「结算周期未知，按 8h 保守估算」。
    funding_periods: int | None = None
    funding_interval_s: float | None = None


def resolve_cost_params(settings, *, rates: tuple[float, float] | None = None,
                        expected_hold_bars: int | None = None) -> CostParams:
    """实盘世界的成本常数（`ai_trade_*` 前缀，与回测世界的 `default_*` 严格分家，§5.1）。

    Args:
        settings: 全局配置。
        rates: 官方费率档位 `(maker_bps, taker_bps)`（轴 A）；`None` ⇒ 用配置常数。**记账链
            （`FeeLedger.params`）与本决策链必须读同一对数字**，否则会出现「prompt 说 8 bps、
            门禁按 12 bps 判」的信任崩塌（A-6 单一真源约束，G-3 断言）。
        expected_hold_bars: 前瞻窗口覆盖（ai-trade-holding-horizon-modes §2.6）；`None` ⇒ **逐字
            回退**全局常数 `ai_trade_cost_expected_hold_bars`。交易周期模式装配后由会话按
            `HorizonRule.expected_hold_bars` 传入，使资金费期数真正随目标持仓时长变化——固定 8 根
            的常数在「趋势持仓 3 天」下会把 9 期资金费算成 1 期。
    """
    maker, taker = rates if rates is not None else (
        float(settings.ai_trade_maker_fee_bps), float(settings.ai_trade_taker_fee_bps))
    return CostParams(
        maker_fee_bps=float(maker),
        taker_fee_bps=float(taker),
        half_spread_fallback_bps=float(settings.ai_trade_half_spread_fallback_bps),
        impact_bps=float(settings.ai_trade_impact_bps),
        expected_hold_bars=int(expected_hold_bars if expected_hold_bars is not None
                               else settings.ai_trade_cost_expected_hold_bars))


def backtest_params(settings, *, expected_hold_bars: int | None = None) -> CostParams:
    """回测世界的成本常数（§3.7）：`SimBroker` 实收的就是这两个数。

    闸 0 链路同构性的正确含义不是「两侧数字相同」而是「两侧都对自己的世界说真话」：`SimBroker`
    收 `default_taker_fee_bps` + `default_slippage_bps` 双边、`allow_limit=False`（回测里 Agent
    只能市价）、且不收资金费率。故 maker 与 taker 同值、冲击成本为 0、半价差取滑点常数。

    `expected_hold_bars` 与 `resolve_cost_params` **同款开口**（holding-horizon [P1-3]）：本函数同样
    硬取过全局常数，不同步开口会让回测按 8 根算资金费、实盘按 12–72 根算，「两侧在同一断面下 reason
    逐字相等」（G-3）不可能通过。`None` ⇒ 逐字回退（既有调用点行为不变）。
    """
    return CostParams(
        maker_fee_bps=float(settings.default_taker_fee_bps),
        taker_fee_bps=float(settings.default_taker_fee_bps),
        half_spread_fallback_bps=float(settings.default_slippage_bps),
        impact_bps=0.0,
        expected_hold_bars=int(expected_hold_bars if expected_hold_bars is not None
                               else settings.ai_trade_cost_expected_hold_bars))


def _finite(value) -> float | None:
    """数值归一：非有限 / 不可转 ⇒ None（绝不把 NaN 带进成本判定）。"""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def funding_periods(query: CostQuery, params: CostParams) -> int:
    """预期持仓期内跨过的资金费结算点数（至少 1 期：跨结算点的持仓要付整期）。

    抽成公开纯函数是为了让 §2.7 的披露文案与成本判定读**同一个数**——在成本模型里算一遍、又在文案
    里按 8h 现算一遍，正是 [R-5] 那类「UI 说一套、判定做另一套」的起点。
    """
    interval = _finite(query.funding_interval_s) or DEFAULT_FUNDING_INTERVAL_S
    hold_s = max(0.0, float(params.expected_hold_bars) * float(query.bar_seconds))
    return max(1, math.ceil(hold_s / interval)) if interval > 0 else 1


def _funding_carry_bps(query: CostQuery, params: CostParams) -> float | None:
    """预期持仓期内的资金费率成本（bps，**只取不利侧**）；`funding_rate is None` ⇒ None。

    `periods = ceil(expected_hold_bars × bar_seconds / funding_interval_s)`（至少 1 期：跨结算点
    的持仓要付整期），`signed = rate × 1e4 × periods × (+1 多 / −1 空)`，取 `max(0.0, signed)`。
    """
    rate = _finite(query.funding_rate)
    if rate is None:
        return None
    signed = (rate * 1e4 * funding_periods(query, params)
              * (1.0 if query.side == "long" else -1.0))
    return max(0.0, signed)


def estimate_round_trip_cost(query: CostQuery, params: CostParams) -> CostBreakdown:
    """一笔往返的全成本（bps，正数=支出）。纯函数、绝不抛。

    `fee_close_bps` **恒取 taker**——开仓时平仓腿的委托类型未知，保守取贵的一侧。

    Args:
        query: 本轮的开仓腿意图 / 方向 / 周期 / 可选盘口与资金费率读数。
        params: 所在世界的成本常数（`resolve_cost_params` 实盘 / `backtest_params` 回测）。

    Returns:
        逐项拆开的 `CostBreakdown`；资金费率缺席时 `funding_carry_bps is None` 且
        `confidence == "degraded"`，此时 `total_bps` **不含**资金费率项（不假装它是 0）。
    """
    ord_type = "limit" if query.ord_type == "limit" else "market"
    fee_open = params.maker_fee_bps if ord_type == "limit" else params.taker_fee_bps
    fee_close = params.taker_fee_bps
    half_spread = _finite(query.half_spread_bps)
    if half_spread is None or half_spread < 0:
        half_spread = params.half_spread_fallback_bps
    impact = max(0.0, params.impact_bps)
    legs = _CROSSING_LEGS[ord_type]
    carry = _funding_carry_bps(query, params)
    total = fee_open + fee_close + legs * (half_spread + impact) + (carry or 0.0)
    return CostBreakdown(
        fee_open_bps=round(fee_open, _ROUND_BPS), fee_close_bps=round(fee_close, _ROUND_BPS),
        half_spread_bps=round(half_spread, _ROUND_BPS), impact_bps=round(impact, _ROUND_BPS),
        crossing_legs=legs,
        funding_carry_bps=None if carry is None else round(carry, _ROUND_BPS),
        total_bps=round(total, _ROUND_BPS),
        confidence="degraded" if carry is None else "ok",
        # 期数恒可算（只依赖 `expected_hold_bars × bar_seconds` 与结算周期），故即使 rate 缺席也如实
        # 下发——「跨几个结算点」与「这几期要付多少」是两件事，前者不该被后者的缺失连坐。
        funding_periods=funding_periods(query, params),
        funding_interval_s=(_finite(query.funding_interval_s)
                            if query.funding_interval_known else None))


def cost_wire(cost: CostBreakdown | None) -> dict | None:
    """`CostBreakdown` → camelCase 只读 wire（进 trace 事件 / payload）；None → None。"""
    if cost is None:
        return None
    return {"feeOpenBps": cost.fee_open_bps, "feeCloseBps": cost.fee_close_bps,
            "halfSpreadBps": cost.half_spread_bps, "impactBps": cost.impact_bps,
            "crossingLegs": cost.crossing_legs, "fundingCarryBps": cost.funding_carry_bps,
            "totalBps": cost.total_bps, "confidence": cost.confidence}


def query_for_preview(*, ord_type: str, side: str, bar_seconds: int, funding,
                      funding_enabled: bool, half_spread_bps: float | None = None) -> CostQuery:
    """由**显式的腿意图与方向**装配 `CostQuery`（决策轮内零网络 IO 的落点）。

    抽出本函数的理由（P0-1）：决策 payload 与系统 prompt 都在**决策尚不存在时**装配，而
    `query_for_decision` 的第一个必填入参就是 `decision`。把「装配 CostQuery」与「从 decision 取值」
    拆开，使轴 D 的预览成本与门禁判定共用**同一对**纯核心，不产生第二条估算路径。

    Args:
        ord_type: 开仓腿意图（`"market"` / `"limit"`）。
        side: 资金费率的收 / 付方向（`"long"` / `"short"`）。
        bar_seconds: 执行周期秒数（资金费率结算期数由它与 `expected_hold_bars` 推）。
        funding: `FundingCache.snapshot()` 产物或 None（陈旧 / 未取到 ⇒ None ⇒ degraded）。
        funding_enabled: 资金费率建模总闸。**关闭时传 `0.0` 而非 `None`**——「有意不建模」不是
            「数据缺失」，二者不得混为一谈（§3.5）。
        half_spread_bps: 盘口半价差（本轮无 books5 订阅 ⇒ None ⇒ 回落常数，D-3）。
    """
    if not funding_enabled:
        return CostQuery(ord_type=ord_type, side=side, bar_seconds=bar_seconds,
                         half_spread_bps=half_spread_bps, funding_rate=0.0)
    if funding is None:
        return CostQuery(ord_type=ord_type, side=side, bar_seconds=bar_seconds,
                         half_spread_bps=half_spread_bps, funding_rate=None)
    return CostQuery(ord_type=ord_type, side=side, bar_seconds=bar_seconds,
                     half_spread_bps=half_spread_bps, funding_rate=funding.rate,
                     funding_interval_s=funding.interval_s, funding_interval_known=True)


def query_for_decision(decision, *, bar_seconds: int, funding, funding_enabled: bool,
                       half_spread_bps: float | None = None) -> CostQuery:
    """`query_for_preview` 的薄包装：方向与腿类型由本轮 `decision` 取（行为逐字节不变）。"""
    return query_for_preview(
        ord_type=decision.ord_type,
        side="long" if decision.action == "open_long" else "short",
        bar_seconds=bar_seconds, funding=funding, funding_enabled=funding_enabled,
        half_spread_bps=half_spread_bps)


def break_even_px(entry_px: float, *, side: str, cost_bps: float) -> float | None:
    """盈亏平衡价 = 开仓价 ×(1 ± cost/1e4)；入参非法 ⇒ `None`。

    **与杠杆无关**：杠杆只放大保证金收益率，不改变回本所需的价格距离。这一点必须写进 prompt 文案，
    否则模型会用杠杆去「稀释」成本（轴 D · D-2）。
    """
    px = _finite(entry_px)
    cost = _finite(cost_bps)
    if px is None or px <= 0 or cost is None or side not in ("long", "short"):
        return None
    return px * (1.0 + cost / 1e4) if side == "long" else px * (1.0 - cost / 1e4)
