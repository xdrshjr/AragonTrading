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
from dataclasses import dataclass, replace

import pandas as pd

from ..engine.simulator import RiskParams, SimBroker
from ..paper.strategy_factory import PaperStrategySpec
from ..strategies.base import Signal
from .decider import TradeDecision
from .signal_prior import TIE_EPS

# 开仓动作枚举（与 `open_funnel._OPEN_ACTIONS` 同值；共识门禁的作用域判据）。
_OPEN_ACTIONS = ("open_long", "open_short")

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
    # 净立场的证据基础（轴 E-1）：both | single_side | none。**末位以外的位置须带默认值**会破坏
    # 既有位置构造，故本字段插在这里、由 `compute_weighted_consensus` 关键字构造统一填充。
    net_stance_basis: str
    weight_long: float
    weight_short: float
    contributors_long: int
    contributors_short: int
    coverage: float | None     # 有立场的板上策略里有先验的比例；全板空仓 ⇒ None（**不是 0.0**）


def compute_weighted_consensus(board: list["StrategySignalView"], priors,
                               *, debias: bool = False) -> WeightedConsensus | None:
    """按方向先验聚合出加权共识；先验缺席 / 两侧皆无贡献者 ⇒ `None` ⇒ payload 整键缺席。

    **本轮不接门禁**：`apply_consensus_gate` 的入参与行为逐字不变，加权结果只进 payload / prompt /
    轨迹与 EV 分子。同时上两道硬共识门禁会让「哪一道在起作用」不可证伪（§2.2）。

    Args:
        board: 本轮信号板。
        priors: `{"long": DirectionPrior|None, "short": DirectionPrior|None}`；`None` ⇒ 返回 None。
        debias: 方向对称性总闸（轴 E-1）。`True` ⇒ 单侧无证据不再按 0.5 参与比较、净立场判 `flat`；
            `False`（默认）⇒ 逐字节回退。

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
        net_stance=_weighted_net_stance(p_long, p_short, debias=debias),
        net_stance_basis=net_stance_basis(p_long, p_short),
        weight_long=0.0 if long_ is None else long_.w_sum,
        weight_short=0.0 if short is None else short.w_sum,
        contributors_long=0 if long_ is None else long_.contributor_n,
        contributors_short=0 if short is None else short.contributor_n,
        coverage=_weighted_coverage(board, priors))


def net_stance_basis(p_long: float | None, p_short: float | None) -> str:
    """净立场的**证据基础**：`both`（两侧都有先验）/ `single_side`（只有一侧）/ `none`（都没有）。

    它是 `netStance` 那个词的资格证明。没有它，「flat」在 wire 上与「两侧都有先验、正好打平」
    长得一模一样，而两者对模型的含义完全相反。
    """
    if p_long is not None and p_short is not None:
        return "both"
    return "none" if p_long is None and p_short is None else "single_side"


def _weighted_net_stance(p_long: float | None, p_short: float | None,
                         *, debias: bool = False) -> str:
    """净立场判定；两侧都有先验时按差值定向，差值不超过 `TIE_EPS` 即 `flat`。

    `debias`（directional-symmetry 轴 E-1）：

    - `False`（默认，总闸关）⇒ **逐字节回退**：无证据的一侧按中性 `0.5` 参与比较。
    - `True` ⇒ 一侧无证据即返回 `"flat"`。理由是这里有一个会自我强化的吸收态：不做空 ⇒ 无做空
      样本 ⇒ `p_short is None` ⇒ 按 0.5 参与比较 ⇒ 只要 `p_long > 0.5` 净立场恒为 `long`
      ⇒ 更不做空。用中性值补一个**不存在的**读数，等于把「没有证据」当成「证据说五五开」，
      而那正是本轮要根除的那种不诚实。

    `p_long` / `p_short` 在 wire 上仍如实为 `None`——把 0.5 写进 wire 会让两者变成同一个数。
    """
    if debias and (p_long is None or p_short is None):
        return "flat"
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
            "netStance": weighted.net_stance,
            # 轴 E-1：让「没有证据」与「证据说五五开」在 wire 上可区分。
            "netStanceBasis": weighted.net_stance_basis,
            "weightLong": round(weighted.weight_long, 4),
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
@dataclass(frozen=True)
class ConsensusRule:
    """共识门禁的会话级不可变配置（随 `AiTradeSession` / `AiBacktestRunner` 生命周期）。

    `contrarian_enabled=False` ⇒ `resolve_consensus_gate` 的行为**逐字等于**旧版三参
    `apply_consensus_gate`（用例 `test_contrarian_disabled_is_byte_identical_to_legacy`）。

    ⚠️ **本对象刻意不带 `position_step`**：步长是决策器的属性、不是纪律阈值，放进规则对象就会
    出现「规则里的 step 与决策器的 step 不是同一个」这种不报错的分叉。它作为 keyword-only 实参
    由调用方从 `decider.position_step` 传入（`session.py` 已有两处同款取法）。

    ⚠️ **`min_consensus` 必须取自 `_resolve_discipline` 之后的那份派生 settings 副本**。在
    `AiTradeSession.__init__` 里抢先快照会读到**旧的 settings 对象**——`api/ai_auto.py` 的
    [P1-2] / G-13 已经把这个顺序钉死过一次，纪律预设的阈值在那之前全是死字母。
    """

    min_consensus: int = 0
    contrarian_enabled: bool = False          # 逆势通路总闸；False ⇒ 行为逐字等于旧版
    contrarian_min_confidence: float = 0.65   # 模型自报置信度下界
    contrarian_max_opposing_votes: int = 1    # 反向票数上限（板子一边倒得太厉害就不放）
    contrarian_min_evidence_chars: int = 60   # `contrarianEvidence` 最小长度（字符数）
    contrarian_max_per_session: int = 3       # 会话级配额
    contrarian_size_mult: float = 0.5         # 强制降档系数（量化前）


@dataclass(frozen=True)
class ConsensusGateOutcome:
    """一次共识门禁判定的完整产物（`reason is None` = 放行）。"""

    decision: TradeDecision
    reason: str | None          # None = 放行
    lane: str                   # "exempt" | "consensus" | "contrarian" | "veto"
    contrarian_used: bool       # True ⇒ 调用方把会话配额 +1
    wire: dict                  # 进 trace / 注记 / payload 的 camelCase 快照


# 逆势通路的五个细分降级码。**全部以 `low_consensus` 开头**是刻意的：`open_funnel._DEGRADE_PREFIXES`
# 与 `discipline_runtime._DEGRADE_PREFIXES` 里的 `low_consensus` 条目因此**零改动**即可把五个码
# 归到 `consensus` 那一格，漏斗继续闭合。细分码是给人看的，归类真源只有一处。
# ⚠️ 第三个消费者必须显式补：`apps/web/lib/aiTrade.ts::DEGRADE_REASON_LABELS` 是降级码中文化的
# 唯一表，且它用 `find(startsWith)`、**顺序敏感**——四个细分码必须排在裸 `low_consensus` 之前。
REASON_OPPOSED = "low_consensus_opposed"
REASON_QUOTA = "low_consensus_quota"
REASON_CONFIDENCE = "low_consensus_conf"
REASON_EVIDENCE = "low_consensus_evidence"
REASON_SIZE = "low_consensus_size"


def _is_finite(value) -> bool:
    """`value` 是不是一个有限实数（fail-closed 判据：非有限 / 非数 ⇒ 假）。"""
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def resolve_consensus_gate(decision: TradeDecision, consensus: Consensus, rule: ConsensusRule,
                           *, position_step: float, used_contrarian: int,
                           bonus_votes: int = 0, opposing_allowance: int = 0,
                           bonus_lane: str = "none") -> ConsensusGateOutcome:
    """共识门禁的**纯核心**（实盘 session 与回测 runner 共用；directional-symmetry 轴 A）。

    ## 这一层解决什么

    旧版判据 `votes >= min_consensus` **看起来对称**（两个方向用同一个阈值），**在物理上却只会
    拦逆势那一侧**：单边行情里信号板必然一边倒，于是「同向票数 >= 1」对顺势方向恒成立、对逆势
    方向恒不成立。它把宪章里那句「你有权推翻任何单一信号」在**开仓这个唯一有意义的场景**里
    变成了空头支票（生产实测：回归窗口 1540 次开仓意愿里 832 次死在本层）。

    本函数保留门禁的保护意图，另给一条**受严格约束、必须降档、有会话配额、对两个方向逐字同构**
    的逆势通路。

    ## 五条不可动摇的不变式（改本函数前先读）

    1. **对两个方向逐字同构**。判定分支里除了 `votes` / `opposing` 的取数那一行，**不允许出现
       任何 `action == "open_short"` 的字面判断**。否则我们只是把偏斜换了个方向。
    2. **只可能更宽、绝不更窄**。逆势通路只在「原本必然被拦死」的轮次生效；它不可能把一个原本
       放行的决策变成 hold，也不可能放大仓位。
    3. **fail-closed**。任一入参缺失 / 非法（`confidence` 非有限、`contrarian_evidence` 非字符串、
       `used_contrarian` 非整数）=> 不走逆势通路，退回原否决。「无法验证」不等于「合规」。
    4. **全部新降级码以 `low_consensus` 开头**（见上方常量注释）。
    5. **`contrarian` 不是降级**。它进漏斗的 `contrarianPassed` 独立计数，**不进 `blocked`**
       （镜像 `sizing_zeroed` 不是一道闸的成文纪律）。

    Args:
        decision: 决策器本轮产出（已过安全回落与置信度门）。
        consensus: 本轮共识度（`compute_consensus` 产物）。
        rule: 本会话的共识门禁配置。
        position_step: 仓位档位步长，真源是 `decider.position_step`（keyword-only）。
        used_contrarian: 本会话**已经**用掉的逆势名额（纯核心不持状态，由调用方持有并传入）。
        bonus_votes: 趋势基调的**顺势追加票**（execution-trend-verdict 轴 D-1a，0 或 1）。
            默认 0 ⇒ 既有全部调用与单测**逐字节回退**（放行轮同样返回同一对象，`is` 相等）。
            它加在 `decision.action` **那一侧**的票上，故其判据**必须含本轮动作**——判定源是
            `trend_verdict.trend_consensus_bonus(action, ...)`，本函数只负责加。
        opposing_allowance: 趋势基调的**顺势反向票上限放宽**（轴 D-1b，0 或 1）。默认 0 ⇒ 同上
            逐字节回退。它**不发票**，只抬高 `_contrarian_block` 首行那条上限；逆势通路的
            逐字证据、置信度下界、会话配额与强制降档**一条不减**。
        bonus_lane: 轴 D-1a 的**五态归因**（`none` / `applied` / `blocked_not_aligned` /
            `blocked_conflict` / `blocked_no_native_vote`）。默认 `"none"` ⇒ wire 与合入前
            形状一致的那一格。**它是入参而不是在这里重判一次**：本函数拿不到 verdict，重判
            必然造出第二份会漂移的判据；判定源是 `trend_verdict.trend_bonus_lane`。

    Returns:
        `ConsensusGateOutcome`。
    """
    bonus = max(0, int(bonus_votes or 0))
    allowance = max(0, int(opposing_allowance or 0))
    max_opposing = rule.contrarian_max_opposing_votes + allowance
    if rule.min_consensus <= 0 or decision.action not in _OPEN_ACTIONS:
        # 豁免轮返回**同一个** decision 对象（`is` 相等），绝不顺手 replace。
        return ConsensusGateOutcome(decision, None, "exempt", False, _consensus_wire(
            "exempt", ConsensusWireFacts(rule, 0, 0, used_contrarian, None, bonus, allowance,
                                         str(bonus_lane or "none"))))
    # ★ 全函数**唯一**一处按方向取数（不变式 1）：此行之下不允许再出现任何方向字面判断。
    long_side = decision.action == "open_long"
    votes = consensus.long_votes if long_side else consensus.short_votes
    opposing = consensus.short_votes if long_side else consensus.long_votes
    effective = votes + bonus                       # 轴 D-1a：顺势追加票（判据不在本函数）
    facts = ConsensusWireFacts(rule, votes, opposing, used_contrarian, None, bonus, allowance,
                               str(bonus_lane or "none"))
    if effective >= rule.min_consensus:
        return ConsensusGateOutcome(decision, None, "consensus", False,
                                    _consensus_wire("consensus", facts))
    tally = f"({effective}/{rule.min_consensus})"
    if not rule.contrarian_enabled:
        # 逐字节回退：降级码与旧版 `apply_consensus_gate` 完全相同。
        return _veto(decision, f"low_consensus{tally}", facts)
    blocked = _contrarian_block(rule, decision, opposing=opposing,
                                used_contrarian=used_contrarian,
                                max_opposing=max_opposing)
    if blocked is not None:
        return _veto(decision, f"{blocked}{tally}", facts)
    # 强制降档（不变式 2：只可能更小）。三种情形一律退回否决，报 `low_consensus_size`：
    #   ① 被档位地板吃到 0 —— 绝不发一张零张的单；
    #   ② 量化向上吸附把仓位放大到原值以上 —— 本通路只可能更宽，绝不可能放大仓位；
    #   ③ **降档被请求了却没有真的发生**（`mult<1` 而量化把它吸附回了原值）。
    # ③ 是实施期发现的方案缺陷（见 spec §实施过程发现的方案缺陷 I-1）：spec 的伪代码只判了
    # `size <= 0`。在 `step=0.5` 这类粗档位上 `0.5×0.5=0.25` 会被吸附回 0.5，于是逆势通路会
    # **按原仓位全额放行**——RK-1 承诺的那道「强制降档 ×0.5」当场失效，且不报任何错。
    proposed = float(decision.size_fraction or 0.0)
    target = proposed * rule.contrarian_size_mult
    size = quantize_size_fraction(target, position_step)
    shrink_requested = rule.contrarian_size_mult < 1.0
    if size <= 0 or size > proposed or (shrink_requested and size >= proposed):
        return _veto(decision, f"{REASON_SIZE}{tally}", facts)
    return ConsensusGateOutcome(replace(decision, size_fraction=size), None, "contrarian", True,
                                _consensus_wire("contrarian", facts))


def _contrarian_block(rule: ConsensusRule, decision: TradeDecision, *, opposing: int,
                      used_contrarian: int, max_opposing: int | None = None) -> str | None:
    """逆势通路的四条件（**顺序即降级码**：先判到哪条就报哪条）；全过 ⇒ `None`。

    fail-closed（不变式 3）：`used_contrarian` 非整数、`confidence` 非有限、证据非字符串，
    三者一律判为不通过——「无法验证」不等于「合规」。

    `max_opposing`（execution-trend-verdict 轴 D-1b）：**只**替换首行那条反向票上限；缺省 ⇒
    取 `rule.contrarian_max_opposing_votes`（逐字节回退）。其余三条判据（会话配额 / 置信度
    下界 / 逐字证据长度）与其后的强制降档**一个字节不动**——本通路放宽的是「门槛之上的
    天花板」，不是门槛本身。
    """
    ceiling = (rule.contrarian_max_opposing_votes if max_opposing is None
               else int(max_opposing))
    if opposing > ceiling:
        return REASON_OPPOSED
    if not isinstance(used_contrarian, int) or used_contrarian >= rule.contrarian_max_per_session:
        return REASON_QUOTA
    if (not _is_finite(decision.confidence)
            or float(decision.confidence) < rule.contrarian_min_confidence):
        return REASON_CONFIDENCE
    evidence = decision.contrarian_evidence
    if not isinstance(evidence, str) or len(evidence.strip()) < rule.contrarian_min_evidence_chars:
        return REASON_EVIDENCE
    return None


@dataclass(frozen=True)
class ConsensusWireFacts:
    """`_consensus_wire` 的聚合入参（评审 RP2-1 的**还债**）。

    本函数合入前已是 6 个位置形参（CLAUDE.md 上限 5，已登记为偏离），
    execution-trend-verdict 又要给它加五键——照旧写法会到 9 个。故同轮收成一个 frozen 结构体，
    签名降到 2 个形参，`.claude-index/config.md` 里那条偏离本轮**已还清**。
    """

    rule: ConsensusRule
    votes: int                 # 策略板**原始**票数（语义不变）
    opposing: int
    used_contrarian: int
    reason: str | None
    bonus_votes: int = 0       # 轴 D-1a 追加票
    opposing_allowance: int = 0  # 轴 D-1b 反向票上限放宽额度
    bonus_lane: str = "none"   # 轴 D-1a 的五态归因（判定源在 `trend_verdict.trend_bonus_lane`）


def _veto(decision: TradeDecision, reason: str,
          facts: ConsensusWireFacts) -> ConsensusGateOutcome:
    """把开仓硬降级为 hold（构造出的 hold 与旧版 `apply_consensus_gate` **逐字相同**）。"""
    hold = TradeDecision(action="hold", size_fraction=None, confidence=None, reason=reason)
    return ConsensusGateOutcome(hold, reason, "veto", False,
                                _consensus_wire("veto", replace(facts, reason=reason)))


def _consensus_wire(lane: str, facts: ConsensusWireFacts) -> dict:
    """门禁判定的 camelCase 只读快照（trace 相位 / `gateVeto` 注记 / payload 共用同一份）。

    **原值与合成值同时下发是硬要求**（execution-trend-verdict §3.6.2）：只发合成值，用户就
    无法回答「这一票是策略给的还是趋势给的」「这单是本来就够格还是被基调放宽了」。故
    `votes` / `contrarianMaxOpposingVotes` 两键**语义不变**（仍是策略板原始票数与配置原值），
    另加五个键说明本轮的放宽。
    """
    rule = facts.rule
    used = facts.used_contrarian if isinstance(facts.used_contrarian, int) else 0
    remaining = max(0, rule.contrarian_max_per_session - used) if rule.contrarian_enabled else 0
    return {"lane": lane, "votes": facts.votes, "opposingVotes": facts.opposing,
            "minConsensus": rule.min_consensus, "reason": facts.reason,
            "contrarianAvailable": bool(rule.contrarian_enabled),
            "contrarianUsed": used, "contrarianRemaining": remaining,
            "contrarianMinConfidence": rule.contrarian_min_confidence,
            "contrarianMinEvidenceChars": rule.contrarian_min_evidence_chars,
            "contrarianMaxOpposingVotes": rule.contrarian_max_opposing_votes,
            "contrarianSizeMult": rule.contrarian_size_mult,
            # ---- 轴 D-1a / D-1b 的五个加法键（execution-trend-verdict §5.3）----
            "trendBonusVotes": int(facts.bonus_votes),
            "effectiveVotes": int(facts.votes) + int(facts.bonus_votes),
            "trendBonusLane": facts.bonus_lane,
            "trendOpposingAllowance": int(facts.opposing_allowance),
            "effectiveMaxOpposingVotes": (rule.contrarian_max_opposing_votes
                                          + int(facts.opposing_allowance))}


def consensus_rule_from_settings(settings) -> ConsensusRule:
    """settings -> `ConsensusRule` 的**唯一**转换点（实盘 session 与回测 runner 共用）。

    ⚠️ 传进来的必须是 `_resolve_discipline` **之后**那份派生 settings 副本
    （`executor.settings` / `runner.settings` 都是它）：纪律预设的 `ai_trade_min_consensus`
    只存在于派生副本上，读进程级单例会拿到一组死字母阈值（`api/ai_auto.py` 的 [P1-2] / G-13）。

    逆势通路要**两个总闸同时开**：方向对称性总闸关掉时整个特性逐字节回退，故它优先。
    """
    return ConsensusRule(
        min_consensus=int(settings.ai_trade_min_consensus),
        contrarian_enabled=bool(settings.ai_trade_directional_symmetry_enabled
                                and settings.ai_trade_consensus_contrarian_enabled),
        contrarian_min_confidence=float(settings.ai_trade_consensus_contrarian_min_confidence),
        contrarian_max_opposing_votes=int(
            settings.ai_trade_consensus_contrarian_max_opposing_votes),
        contrarian_min_evidence_chars=int(
            settings.ai_trade_consensus_contrarian_min_evidence_chars),
        contrarian_max_per_session=int(settings.ai_trade_consensus_contrarian_max_per_session),
        contrarian_size_mult=float(settings.ai_trade_consensus_contrarian_size_mult))


def contrarian_schema(rule: ConsensusRule):
    """`ConsensusRule` -> 决策工具 schema 的三个阈值；通路不可用 ⇒ `None` ⇒ schema 逐字节回退。

    实盘与回测**共用本函数**：只在一处露出 `contrarianEvidence`，另一处的逆势通路就恒不可达
    （证据字段永远为 None ⇒ `low_consensus_evidence`），而两边的行为分叉不会报任何错。

    返回类型是 `prompts.ContrarianSchemaOptions | None`——**刻意不写进注解**：模块级引用
    `prompts` 会造出 `signals → prompts → signals` 的 import 环（`position_ladder` 那条延迟导入
    正是为它存在的）。

    Returns:
        `ContrarianSchemaOptions` 或 `None`（通路不可用）。
    """
    if not rule.contrarian_enabled or rule.min_consensus <= 0:
        return None
    from .prompts import ContrarianSchemaOptions   # 延迟导入打破 signals→prompts 模块级循环

    return ContrarianSchemaOptions(min_chars=rule.contrarian_min_evidence_chars,
                                   min_confidence=rule.contrarian_min_confidence,
                                   size_mult=rule.contrarian_size_mult)


def contrarian_payload(rule: ConsensusRule, used_contrarian: int) -> dict | None:
    """`payload["consensus"]["contrarian"]`：**让模型知道这条通路存在**（A-5）。

    没有这一段，逆势通路就是一个「有写点没读点」的功能：模型不知道存在这条门槛、不知道差几票、
    也不知道要写 `contrarianEvidence` 才能走。通路不可用 ⇒ 返回 `None` ⇒ **整键缺席** ⇒
    prompt 逐字节回退。
    """
    if not rule.contrarian_enabled or rule.min_consensus <= 0:
        return None
    used = used_contrarian if isinstance(used_contrarian, int) else 0
    return {"available": True,
            "minConfidence": rule.contrarian_min_confidence,
            "minEvidenceChars": rule.contrarian_min_evidence_chars,
            "maxOpposingVotes": rule.contrarian_max_opposing_votes,
            "remaining": max(0, rule.contrarian_max_per_session - used),
            "sizeMult": rule.contrarian_size_mult}


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

    **三参旧签名保留不动，实现已降为 `resolve_consensus_gate` 的薄封装**
    （directional-symmetry 轴 A）：`ConsensusRule(min_consensus=...)` 的
    `contrarian_enabled` 默认 `False` ⇒ 逆势通路不参与 ⇒ 既有单测与任何未迁移调用点
    **逐字节回退**（放行轮同样返回同一个对象，`is` 相等）。
    """
    out = resolve_consensus_gate(decision, consensus, ConsensusRule(min_consensus=min_consensus),
                                 position_step=0.0, used_contrarian=0)
    return out.decision, out.reason


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
