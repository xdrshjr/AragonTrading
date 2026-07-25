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

import math
from dataclasses import dataclass

import pandas as pd

from ..engine.simulator import RiskParams, SimBroker
from ..paper.strategy_factory import PaperStrategySpec
from ..strategies.base import Signal
from .decider import TradeDecision
from .signal_prior import TIE_EPS

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


# ============================ 加权共识（signal-prior-and-ev-gate §3.8，与等权**并列**） ============================
@dataclass
class WeightedConsensus:
    """IR 加权共识：把「几票」换成「这些票历史上值多少钱」（`compute_consensus` 一字不动）。"""

    p_long: float | None       # 该方向无贡献者 ⇒ None（**不是 0.5**）
    p_short: float | None
    net_stance: str            # long | short | flat（差值不超过 TIE_EPS 即 flat）
    weight_long: float
    weight_short: float
    contributors_long: int
    contributors_short: int
    coverage: float | None     # 有立场的板上策略里有先验的比例；全板空仓 ⇒ None（**不是 0.0**）


def compute_weighted_consensus(board: list["StrategySignalView"],
                               priors) -> WeightedConsensus | None:
    """按方向先验聚合出加权共识；先验缺席 / 两侧皆无贡献者 ⇒ `None` ⇒ payload 整键缺席。

    **本轮不接门禁**：`apply_consensus_gate` 的入参与行为逐字不变，加权结果只进 payload / prompt /
    轨迹与 EV 分子。同时上两道硬共识门禁会让「哪一道在起作用」不可证伪（§2.2）。

    Args:
        board: 本轮信号板。
        priors: `{"long": DirectionPrior|None, "short": DirectionPrior|None}`；`None` ⇒ 返回 None。

    Returns:
        `WeightedConsensus` 或 `None`。
    """
    if not priors:
        return None
    long_, short = priors.get("long"), priors.get("short")
    if long_ is None and short is None:
        return None
    p_long = None if long_ is None else long_.p
    p_short = None if short is None else short.p
    return WeightedConsensus(
        p_long=p_long, p_short=p_short,
        net_stance=_weighted_net_stance(p_long, p_short),
        weight_long=0.0 if long_ is None else long_.w_sum,
        weight_short=0.0 if short is None else short.w_sum,
        contributors_long=0 if long_ is None else long_.contributor_n,
        contributors_short=0 if short is None else short.contributor_n,
        coverage=_weighted_coverage(board, priors))


def _weighted_net_stance(p_long: float | None, p_short: float | None) -> str:
    """净立场判定：无证据的一侧按中性 0.5 **参与比较**，但 `p_long`/`p_short` 仍如实为 `None`。

    把 0.5 写进 wire 会让「没有证据」和「证据说五五开」变成同一个数；只在比较这一处用中性值，
    是为了让「只有一侧有先验」的轮次仍能给出方向，而不是一律 flat。
    """
    lo = 0.5 if p_long is None else p_long
    sh = 0.5 if p_short is None else p_short
    if lo - sh > TIE_EPS:
        return "long"
    if sh - lo > TIE_EPS:
        return "short"
    return "flat"


def board_prior_coverage(board: list["StrategySignalView"],
                         priors) -> tuple[int, int] | None:
    """覆盖率的**唯一口径**（比值与计数两个读点共用）：`(有先验的, 本轮在说话的)`。

    **分母只数本轮真的在说话的策略**（`stance ∈ {long, short}`）。把 `stance == flat` 的策略也算
    进分母，会让「覆盖率 ≥ 80%」在一个健康系统上永远不达标——空仓策略本就不该有方向先验，运维
    据此会去做错误的补救（拉长回看窗口）。全板空仓 ⇒ `None`（**不是 0/0**）：没有分母时报 0%
    是在制造一个假的故障信号。
    """
    speaking = [v for v in board if v.stance in ("long", "short")]
    if not speaking:
        return None
    covered = {d: set(getattr((priors or {}).get(d), "strategy_ids", ()) or ())
               for d in ("long", "short")}
    hit = sum(1 for v in speaking if v.strategy_id in covered[v.stance])
    return hit, len(speaking)


def _weighted_coverage(board: list["StrategySignalView"], priors) -> float | None:
    """覆盖率比值（口径见 `board_prior_coverage`；全板空仓 ⇒ `None` 而非 `0.0`）。"""
    counts = board_prior_coverage(board, priors)
    return None if counts is None else round(counts[0] / counts[1], 4)


def weighted_consensus_wire(weighted: WeightedConsensus | None) -> dict | None:
    """`WeightedConsensus` → camelCase 只读 wire；`None` → `None`（键缺席即整段省略）。"""
    if weighted is None:
        return None
    return {"pLong": weighted.p_long, "pShort": weighted.p_short,
            "netStance": weighted.net_stance, "weightLong": round(weighted.weight_long, 4),
            "weightShort": round(weighted.weight_short, 4),
            "contributorsLong": weighted.contributors_long,
            "contributorsShort": weighted.contributors_short,
            "coverage": weighted.coverage}


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

    `open_long/open_short` 带 `size_fraction` 作 `strength`；`close` 带理由；其余一律 `[]`。
    `TradeDecision.size_fraction` 为 snake_case（决策器 dataclass 字段）。

    **显式 action 白名单（ai-trade-limit-orders §2.2a / M-12，P0-1）**：本函数此前的末行是裸兜底
    `return [Signal(side="close", …)]`——任何未知 action 都会被**静默**翻译成平仓信号。加入新动作
    `cancel_order` 后，一旦 session 层漏了前置分发，Agent 说「撤掉我那笔没成交的挂单」就会被系统
    执行成「市价平掉真实持仓」：不抛异常、不留错误、盈亏照记，事后只能靠人肉看流水才发现。

    故改为白名单：未列出的动作（含 `hold` / `cancel_order` / 未来新增动作 / 空串）一律返回 `[]`
    ——空信号即「什么都不做」。**兜底的方向必须是「不动」，不能是「动」**：`hold` 之所以能当全部
    降级路径的落点，正因为它无副作用；一个会下真单的兜底分支不配做兜底。

    Args:
        decision: 本轮最终决策（共识门禁后）。

    Returns:
        供 `SimBroker.submit` / `LiveExecutor.apply` 消费的信号列表；非交易动作 → `[]`。
    """
    action = decision.action
    if action == "open_long":
        return [Signal(side="long", strength=decision.size_fraction or 0.0, reason=decision.reason)]
    if action == "open_short":
        return [Signal(side="short", strength=decision.size_fraction or 0.0, reason=decision.reason)]
    if action == "close":
        return [Signal(side="close", reason=decision.reason or "ai_close")]
    return []      # hold / cancel_order / 未知动作：绝不兜底成 close（P0-1）


# ============================ 仓位操作档位（ai-trade-position-sizing-steps §2.2，实盘/回测共用）============================
_STEP_EPS = 1e-9


def position_ladder(step: float) -> list[float]:
    """档位阶梯的**唯一真源**（[R-5]）：`{k·step | k=1..max_gears}`，顶格 ≤ 100%。

    `quantize_size_fraction` 的顶格、决策工具 schema 的 `enum`、系统 prompt 的档位文案、前端
    `positionLadder` 全部由本函数派生，杜绝三处各自 `floor(1/step)` 漂移、破坏「顶格恰为 100%」不变式。

    Args:
        step: 档位步长（fraction，如 0.25=每格 25%）。`step<=0` → 连续/不约束（无离散档位）。

    Returns:
        升序档位列表；`step<=0` 时为 `[]`。`max_gears=floor((1+eps)/step)` 保证顶格不越 100%
        （step=0.25→`[.25,.5,.75,1.0]`；step=0.3→`[.3,.6,.9]`，故预设只放能整除 100 的 10/20/25/50/100）。
    """
    if step <= 0:
        return []
    max_gears = int(math.floor((1.0 + _STEP_EPS) / step))
    return [round(k * step, 6) for k in range(1, max_gears + 1)]


def quantize_size_fraction(raw: float, step: float) -> float:
    """把开仓仓位占比吸附到最近的合法档位（`step` 的整数倍），夹到 `[step, 顶格]`（唯一硬约束点）。

    `step<=0` → 连续（仅夹到 `[0,1]` 直通，逐字等价旧行为）。`max(1, …)` 使任何 `>0` 的开仓意图**至少**
    落到最小一格——绝不因四舍五入到 0 被 `_map_decision` 误判 `missing_size`（回退仍只由 `_clamp01` 的
    `None/≤0` 决定，回归安全）。顶格取自 `position_ladder`（≤100%），与真源同源封顶。

    Args:
        raw: LLM 给出的开仓仓位占比（已过 `_clamp01`，`∈[0,1]`）。
        step: 档位步长（fraction）。

    Returns:
        吸附后的合法档位值（必 `∈ position_ladder(step)`）；`step<=0` 时为 `clamp01(raw)`。
    """
    lo = max(0.0, min(raw, 1.0))
    ladder = position_ladder(step)
    if not ladder:                       # step<=0：连续直通
        return lo
    gears = max(1, min(round(lo / step), len(ladder)))   # 顶格 = 阶梯长度（真源一致）
    return ladder[gears - 1]
