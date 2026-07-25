"""最小 edge 门禁纯核心（ai-trade-cost-accounting-and-edge-gate §3.6 / §3.7）。

仓库里已有的三层确定性保护（`base_rules` 铁律、`reflection` 下单前反思、`profit_guard` 利润保护
棘轮）解决的都是归因恒等式 `E = p·W − (1−p)·L − C` 左侧的「少犯错」，**没有任何一层回答「这笔交易
的期望收益是否覆盖成本」**。本模块就是那一层：`毛 edge(bps) ≥ k_min × C(bps)` 不成立即把开仓降级
为观望。

四个不可动摇的不变式（改本文件前先读）：
1. **只会更保守**：唯一可能的输出变化是 `open_* → hold`；`close` / `cancel_order` / `hold` 永不被
   触碰（豁免矩阵与 `base_rules._GATED_ACTIONS` 逐字同构）。
2. **fail-open**（与铁律层相反，与利润保护层相同）：估算不可用 ⇒ **放行** + 计 `unavailable`。
   理由见 §3.7——本层输入是 ATR，而 `ewm(min_periods=period)` 的前 `period−1` 行恒为 NaN，一个
   fail-closed 的粗估门禁会在暖机断面上把开仓全部否掉，那正是本仓库发生过的「反思致永不开单」事故。
3. **`k_min <= 0` ⇒ 门禁空转**：返回**同一个** decision 对象（`is` 相等），绝不顺手 `replace`。
4. **判定价单点收敛**：复用 `base_rules.entry_reference_px`（限价判委托价、市价判最新价），
   **严禁**在本模块另写一份判定价逻辑。

阶段一的 edge 分子是**显式标注的粗估**：`min(atr_target_mult × ATR(bps), 单仓 OCO 止盈距离(bps))`。
刻意不用 `decision.confidence`——D-7 已确认置信度未校准，把未校准的胜率先验当门禁输入会把 D-7 的
错误传播进来且无法证伪。ATR 在实盘与回测两侧都已存在且共用同一条降级判据（`sanitize_atr`）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .base_rules import entry_reference_px
from .cost_model import (
    CostBreakdown,
    cost_wire,
    estimate_round_trip_cost,
    query_for_decision,
    resolve_cost_params,
)
from .decider import TradeDecision
from .profit_guard import sanitize_atr

# 受门禁约束的动作（豁免矩阵冻结，与 `base_rules._GATED_ACTIONS` 逐字同构）。
_GATED_ACTIONS = ("open_long", "open_short")

_ROUND_BPS = 6


@dataclass(frozen=True)
class EdgeEstimate:
    """本轮的粗估毛 edge（bps）及其来源，供 UI / 归因如实标注「这只是粗估」。"""

    target_bps: float
    source: str                         # "atr" | "take_profit"
    atr_bps: float | None
    take_profit_bps: float | None


@dataclass(frozen=True)
class EdgeGateOutcome:
    """一轮门禁的完整产物（实盘薄封装用；回测直接用下方两个低阶纯函数）。"""

    decision: TradeDecision
    reason: str | None
    status: str                         # pass | veto | unavailable
    wire: dict                          # trace 事件 data（camelCase）
    cost_bps: float | None
    edge_bps: float | None
    # 本轮是否**受门禁约束**（动作在豁免矩阵之外）。观望 / 平仓 / 撤单轮的 `status` 也是 `pass`
    # ——门禁无事可做，但它**不该进 `edgeGate` 的计数分母**：AI 会话的观望轮占比很高，把它们记成
    # 「放行」会把 `vetoRate` 稀释到 §7.5 的 L2 放行区间（20%~70%）以下，运维据此会误判「门禁未生效」。
    gated: bool


def estimate_gross_edge_bps(*, action: str, ref_px: float | None, atr_px: float | None,
                            take_profit_pct: float | None,
                            atr_target_mult: float) -> EdgeEstimate | None:
    """粗估本轮开仓的毛 edge（bps）；非受控动作 / 判定价或 ATR 不可用 ⇒ `None`（⇒ fail-open）。

    上界项是**单仓 OCO 止盈距离**（实盘取 `executor.risk.take_profit_pct`、回测取
    `runner._oco_risk.take_profit_pct`）——模型再有想法也拿不到超过 OCO 止盈的利润，故它只
    **收窄**、绝不放大 edge。**严禁误接 `session.take_profit_usd`**（那是会话级 USD 金额阈值，
    量纲完全不同，错接会产生一个数值上「看起来合理」却毫无意义的上界）。

    `take_profit_pct is None`（默认允许为空）⇒ 上界**整体不生效**，退化为纯 ATR 预算、
    `source == "atr"`、`take_profit_bps is None`——`None` 是「不设上界」而非「上界为 0」。

    Args:
        action: 本轮决策动作（仅 `open_long`/`open_short` 需要估算）。
        ref_px: 判定价（调用方经 `base_rules.entry_reference_px` 解析，限价即委托价）。
        atr_px: 确认根 ATR（价格单位）；`None`/非有限/≤0 ⇒ 估算不可用。
        take_profit_pct: 单仓 OCO 止盈比例（fraction）；`None` ⇒ 不设上界。
        atr_target_mult: ATR 预算倍数（`ai_trade_edge_atr_target_mult`）。

    Returns:
        `EdgeEstimate`；无法估算时 `None`。
    """
    if action not in _GATED_ACTIONS:
        return None
    px = _positive(ref_px)
    atr = sanitize_atr(atr_px)      # 与实盘 safe_atr / 回测 _atr_at 同一条降级判据（§3.6）
    mult = _positive(atr_target_mult)
    if px is None or atr is None or mult is None:
        return None
    atr_bps = atr / px * 1e4
    target, source = atr_bps * mult, "atr"
    tp_bps = _positive(take_profit_pct)
    tp_bps = None if tp_bps is None else tp_bps * 1e4
    if tp_bps is not None and tp_bps < target:
        target, source = tp_bps, "take_profit"
    return EdgeEstimate(target_bps=round(target, _ROUND_BPS), source=source,
                        atr_bps=round(atr_bps, _ROUND_BPS),
                        take_profit_bps=None if tp_bps is None else round(tp_bps, _ROUND_BPS))


def apply_edge_gate(decision: TradeDecision, edge: EdgeEstimate | None,
                    cost: CostBreakdown | None, *, k_min: float,
                    degraded_mult: float) -> tuple[TradeDecision, str | None]:
    """最小 edge 门禁：期望毛收益覆盖不了 `k_min × C` 的开仓轮硬降级为 hold（仅更保守）。

    `k_min <= 0`、动作在豁免矩阵内、或估算不可用时返回**同一个** decision 对象（`is` 相等）。

    Returns:
        `(decision, gate_reason)`——`gate_reason` 非 None 表示本轮被 edge 门禁降级为 hold，
        取值形如 `edge_below_cost(edge=8.1bps,cost=13.0bps,k=2.0)`（与既有 `low_consensus(k/n)`、
        `base_rule_ma_band(pctB=0.42)` 同为带参数后缀的前缀码风格）。
    """
    if k_min <= 0 or decision.action not in _GATED_ACTIONS:
        return decision, None
    if edge is None or cost is None:            # 估算不可用 ⇒ fail-open（§3.7 的极性论证）
        return decision, None
    required = k_min * cost.total_bps * degraded_multiplier(cost, degraded_mult)
    if edge.target_bps >= required:
        return decision, None
    reason = (f"edge_below_cost(edge={edge.target_bps:.1f}bps,"
              f"cost={cost.total_bps:.1f}bps,k={k_min:.1f})")
    return _veto(reason)


def _veto(reason: str) -> tuple[TradeDecision, str]:
    """被 edge 门禁否决：本轮开仓不发生，降级为 hold（与铁律层的 `_veto` 逐字同构）。"""
    return TradeDecision(action="hold", size_fraction=None, confidence=None,
                         reason=reason), reason


def gate_status(action: str, edge: EdgeEstimate | None, cost: CostBreakdown | None,
                gate_reason: str | None) -> str:
    """轨迹段状态：被否 → `veto`；受控动作但估算不可用 → `unavailable`；其余 → `pass`。

    **`action` 是 v2 spec 之外的必要形参**（见 spec §十二 实施发现 I-1）：门禁每轮都发一条轨迹
    事件，若不看动作，一个持续 hold 的会话会被记成连续 `unavailable` 并误触发
    `AI_TRADE_EDGE_GATE_UNAVAILABLE` 告警——而那条告警的文案说的是「期间开仓一律放行」。
    """
    if gate_reason:
        return "veto"
    if action not in _GATED_ACTIONS:
        return "pass"
    return "unavailable" if (edge is None or cost is None) else "pass"


def edge_wire(edge: EdgeEstimate | None) -> dict | None:
    """`EdgeEstimate` → camelCase 只读 wire；None → None。"""
    if edge is None:
        return None
    return {"targetBps": edge.target_bps, "source": edge.source,
            "atrBps": edge.atr_bps, "takeProfitBps": edge.take_profit_bps}


def evaluate_edge_gate(decision: TradeDecision, *, settings, last_px: float | None,
                       atr_px: float | None, take_profit_pct: float | None,
                       bar_seconds: int, funding=None, params=None,
                       funding_enabled: bool | None = None) -> EdgeGateOutcome | None:
    """一轮门禁的**完整**编排（取成本 → 估 edge → 判定 → 装配 wire）；`k_min <= 0` ⇒ `None`。

    存在的唯一理由是把业务逻辑留在本模块、让 `session.py` 只剩薄封装调用（spec §3.10 的行数
    预算是硬验收判据，Z-8）。`None` 即「门禁空转」——调用方据此不发任何事件、不动任何计数。

    Args:
        decision: 本轮决策（门禁前）。
        settings: 运行配置（提供 `k_min` / `degraded_mult` / `atr_target_mult` 与成本常数）。
        last_px: 本轮最新价（限价决策会被 `entry_reference_px` 改判为委托价）。
        atr_px: 确认根 ATR（价格单位）。
        take_profit_pct: 单仓 OCO 止盈比例（`None` ⇒ 上界不生效）。
        bar_seconds: 执行周期秒数。
        funding: 会话级资金费率快照或 None。
        params: 成本常数覆盖（回测传 `cost_model.backtest_params(settings)`）。
        funding_enabled: 资金费率总闸覆盖（回测传 `False`：SimBroker 不收资金费率，是**有意
            不建模**而非数据缺失，故 `confidence` 应保持 `"ok"`）。
    """
    k_min = float(settings.ai_trade_edge_gate_k_min)
    if k_min <= 0:
        return None
    cost = estimate_round_trip_cost(
        query_for_decision(
            decision, bar_seconds=bar_seconds, funding=funding,
            funding_enabled=(settings.ai_trade_funding_enabled
                             if funding_enabled is None else funding_enabled)),
        params or resolve_cost_params(settings))
    edge = estimate_gross_edge_bps(
        action=decision.action, ref_px=entry_reference_px(decision, last_px), atr_px=atr_px,
        take_profit_pct=take_profit_pct,
        atr_target_mult=float(settings.ai_trade_edge_atr_target_mult))
    action = decision.action                    # 门禁**前**的动作：豁免矩阵与状态判定都按它
    decided, reason = apply_edge_gate(
        decision, edge, cost, k_min=k_min,
        degraded_mult=float(settings.ai_trade_edge_gate_degraded_mult))
    status = gate_status(action, edge, cost, reason)
    mult = degraded_multiplier(cost, float(settings.ai_trade_edge_gate_degraded_mult))
    wire = {"status": status, "edgeBps": None if edge is None else edge.target_bps,
            "costBps": cost.total_bps, "kMin": k_min,
            "requiredBps": round(k_min * cost.total_bps * mult, _ROUND_BPS),
            "costConfidence": cost.confidence,
            "edgeSource": None if edge is None else edge.source,
            "cost": cost_wire(cost), "edge": edge_wire(edge), "reason": reason}
    return EdgeGateOutcome(decision=decided, reason=reason, status=status, wire=wire,
                           cost_bps=cost.total_bps,
                           edge_bps=None if edge is None else edge.target_bps,
                           gated=action in _GATED_ACTIONS)


def degraded_multiplier(cost: CostBreakdown | None, degraded_mult: float) -> float:
    """成本估算降级时的阈值收紧倍数（`cost is None` 或 `"ok"` ⇒ 1.0）。"""
    return degraded_mult if cost is not None and cost.confidence == "degraded" else 1.0


def _positive(value) -> float | None:
    """正有限数归一：非有限 / ≤0 / 不可转 ⇒ None。"""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) and out > 0 else None
