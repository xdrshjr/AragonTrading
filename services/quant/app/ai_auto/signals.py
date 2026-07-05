"""多策略信号板：每策略独立 `SimBroker` 影子回放求**持久立场**（ai-auto-trading §2.2 / P0-1）。

为何不能「读单根 `on_candle` 返回值当立场」（P0-1 证伪）：`strategies/base.on_candle` 是**边沿触发**
——仅在事件根返回一条 `Signal`，其余根返回 `[]`；且 `Signal.side` 只有 `{long,short,close}`，**无中性值**。
故「无信号即 none」会使信号板几乎恒为 none，AI 无从综合研判；且状态化策略的 `close` 只有在它沿**自己**
的成交轨迹递推内部状态后才可达。

正确机制：为**每个**选中策略各维护一个独立 `engine.simulator.SimBroker`（纸面/回测同款、已测的确定性
执行器），让该策略在**自己的**影子仓位上逐根演进（`paper/session._process_confirmed` 同款四阶段驱动）；
其「立场（stance）」即该影子 broker 的 `position_side`——一个持久、真有意义的多/空/空仓状态。影子 broker
**只为读立场，绝不下任何真单**；真单一律只经 AI 决策 → `LiveExecutor`（§2.3），二者严格隔离。

实现级勘误（见 spec「## 实施过程发现的方案缺陷」）：`SimBroker.context()` 无参（非 `context(i, ref_px)`）；
暖机与实时段统一用窗口位置索引 `i=len(candles)-1`，避免 deque 滑窗导致 `entry_bar` 漂移。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..engine.simulator import RiskParams, SimBroker
from ..paper.strategy_factory import PaperStrategySpec
from ..strategies.base import Signal
from .decider import TradeDecision

# 影子 broker 名义资金（仅用于读立场；stance 只取决于开/平信号，与具体权益无关）。
_SHADOW_CAPITAL = 10_000.0
_SHADOW_FEE_BPS = 5.0
_SHADOW_SLIP_BPS = 2.0


@dataclass
class StrategySignalView:
    """单策略此刻喂给 AI 的立场视图（P0-1：stance=影子持久立场，latest_signal=本根边沿）。"""

    strategy_id: int
    name: str
    stance: str            # long | short | flat（影子 broker 持久立场）
    latest_signal: str     # open_long | open_short | close | none（本根 on_candle 边沿）
    strength: float        # 本根信号仓位强度（无信号=0）
    reason: str            # 本根触发原因（marker tooltip 同源）
    risk_params: RiskParams  # 策略风控（供 AI 参考，不入 WS 线格式）


@dataclass
class Consensus:
    """策略共识度（E2）：信号板逐策略持久立场聚合出的多/空/空仓票 + 净立场 + 一致度。"""

    long_votes: int
    short_votes: int
    flat_votes: int
    total: int
    net_stance: str        # long | short | flat（多空票差决定；相等 → flat）
    agreement: float       # max(long,short)/total ∈ [0,1]（最大同向占比；空板 → 0）


def compute_consensus(board: list["StrategySignalView"]) -> Consensus:
    """把信号板逐策略持久立场 `stance` 聚合为共识度（纯聚合、无副作用、易单测，E2）。

    Args:
        board: 本轮信号板（每项 `stance ∈ {long, short, flat}`）。

    Returns:
        `Consensus`——票数分布 + 净立场（多空票差；相等即 flat）+ 一致度（最大同向占比）。
    """
    long_votes = sum(1 for v in board if v.stance == "long")
    short_votes = sum(1 for v in board if v.stance == "short")
    flat_votes = sum(1 for v in board if v.stance == "flat")
    total = len(board)
    if long_votes > short_votes:
        net_stance = "long"
    elif short_votes > long_votes:
        net_stance = "short"
    else:
        net_stance = "flat"
    agreement = max(long_votes, short_votes) / total if total else 0.0
    return Consensus(long_votes=long_votes, short_votes=short_votes, flat_votes=flat_votes,
                     total=total, net_stance=net_stance, agreement=round(agreement, 4))


def make_shadow_broker(leverage: float = 1.0) -> SimBroker:
    """构造一个影子 `SimBroker`（每策略一个，读立场专用）。"""
    return SimBroker(_SHADOW_CAPITAL, taker_fee_bps=_SHADOW_FEE_BPS,
                     slippage_bps=_SHADOW_SLIP_BPS, leverage=max(leverage, 0.0) or 1.0)


def _side_to_latest(side: str) -> str:
    """把边沿 `Signal.side` 映射为信号板 `latestSignal` 词汇（open_long/open_short/close）。"""
    return {"long": "open_long", "short": "open_short", "close": "close"}.get(side, "none")


def step_strategy_shadow(spec: PaperStrategySpec, shadow: SimBroker,
                         df: pd.DataFrame, i: int) -> StrategySignalView:
    """驱动单策略影子 broker 一根（四阶段，镜像 `paper/session._process_confirmed`），返回立场视图。

    顺序：`set_risk → fill_pending(本根开盘成交上一根排队单) → apply_risk(本根风控) → mark(盯市)`；
    暖机阈值后 `context()`（读上一根成交后的持仓）→ `on_candle`（边沿信号）→ `submit`（排队次根）。
    `stance` 取 `submit` 前后不变的 `shadow.pos.side`（持久立场）；`latest_signal` 取本根边沿。
    """
    strategy = spec.strategy
    risk = strategy.risk_params()
    row = df.iloc[i]
    shadow.set_risk(risk)
    shadow.fill_pending(open_price=float(row["o"]), ts=int(row["ts"]), bar_index=i)
    shadow.apply_risk(i, row, risk)
    shadow.mark(ts=int(row["ts"]), close=float(row["c"]))

    latest, strength, reason = "none", 0.0, ""
    if i >= strategy.warmup():
        signals = strategy.on_candle(df, i, shadow.context())
        if signals:
            shadow.submit(signals)
            sig = signals[-1]
            latest = _side_to_latest(sig.side)
            strength = float(getattr(sig, "strength", 1.0) or 0.0)
            reason = getattr(sig, "reason", "") or ""
    return StrategySignalView(
        strategy_id=spec.strategy_id, name=spec.strategy_name,
        stance=shadow.pos.side, latest_signal=latest, strength=strength,
        reason=reason, risk_params=risk)


def evaluate_signal_board(specs: list[PaperStrategySpec], shadows: list[SimBroker],
                          dfs: list[pd.DataFrame], i: int) -> list[StrategySignalView]:
    """逐策略影子回放求立场，组成本轮信号板（各策略只看自己的影子，互不污染，P0-1）。

    `specs`/`shadows`/`dfs` 三者按策略一一对齐；`dfs[k]` 是策略 k 自己 `prepare`(+MTF) 后的帧。
    暖机段调用本函数但丢弃返回值（仅为逐根种入影子与策略内部状态）；实时段用返回的视图喂决策器。
    """
    return [step_strategy_shadow(spec, shadow, df, i)
            for spec, shadow, df in zip(specs, shadows, dfs)]


# ============================ 共识门禁 + 决策→信号（session 与 backtest 共用核心，§2.8/R1） ============================
def apply_consensus_gate(decision: TradeDecision, consensus: Consensus,
                         min_consensus: int) -> tuple[TradeDecision, str | None]:
    """共识不足则把开仓硬降级为 hold（E2 补偿控制，独立于 LLM，仅更保守）。

    `min_consensus` 为**显式入参**（调用方从 settings 或用户覆盖值传入，§2.8/R1），使本函数为纯函数，
    供实盘 session 与回测 runner 共用。门禁只作用于 `open_long/open_short`——`close`/`hold` 从不被拦
    （保证「共识不足也永远能平仓保护」）。

    Args:
        decision: 决策器本轮产出（已过安全回落与置信度门）。
        consensus: 本轮共识度（`compute_consensus` 产物）。
        min_consensus: 开仓最小同向策略数门槛；≤0 即关闭门禁。

    Returns:
        `(decision, gate_reason)`——`gate_reason` 非 None 表示本轮被共识门禁降级为 hold
        （带 `low_consensus(k/n)` 计数）；未降级则 `gate_reason is None`。
    """
    if min_consensus <= 0 or decision.action not in ("open_long", "open_short"):
        return decision, None
    votes = consensus.long_votes if decision.action == "open_long" else consensus.short_votes
    if votes < min_consensus:
        reason = f"low_consensus({votes}/{min_consensus})"
        hold = TradeDecision(action="hold", size_fraction=None, confidence=None, reason=reason)
        return hold, reason
    return decision, None


def decision_to_signals(decision: TradeDecision) -> list[Signal]:
    """决策 → `Signal` **纯核心映射**（无 explain 富化；session 调本核心后再补富化，§2.8/R1）。

    `open_long/open_short` 带 `size_fraction` 作 `strength`；`close` 带理由；`hold` 为空列表。
    `TradeDecision.size_fraction` 为 snake_case（决策器 dataclass 字段）。

    Args:
        decision: 本轮最终决策（共识门禁后）。

    Returns:
        供 `SimBroker.submit` / `LiveExecutor.apply` 消费的信号列表（hold → `[]`）。
    """
    action = decision.action
    if action == "hold":
        return []
    if action == "open_long":
        return [Signal(side="long", strength=decision.size_fraction or 0.0, reason=decision.reason)]
    if action == "open_short":
        return [Signal(side="short", strength=decision.size_fraction or 0.0, reason=decision.reason)]
    return [Signal(side="close", reason=decision.reason or "ai_close")]
