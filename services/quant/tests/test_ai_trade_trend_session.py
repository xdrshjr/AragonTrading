"""执行周期趋势基调的**会话级**贯通（ai-trade-execution-trend-verdict §7.3）。

纯函数单测看不见的四件事，本文件逐条钉：

- **S-1**：payload 的装配顺序（`trendVerdict` 必须排在 `tacticalContext` **之后**，否则
  `conflict` 恒 `"none"`、战术背离永不生效，**且不报任何错**）。
- **S-5**：总闸关 ⇒ 全链路**逐字节回退**（payload 无键、决策 prompt sha256 与合入前相同）。
- **S-6b / S-6c（评审 RP0-5 的回归护栏）**：alignment 在**开仓委托发出的那一刻定格**，
  跨轮成交与几十轮后的结算都从那张认领表取。照 v1 的 `note_fill(side, alignment, pnl)`
  实现，S-6b 必红而单轮用例恒绿。
- **S-7（评审 RP1-1）**：开仓意愿的分母判据**逐字复用** `open_funnel.wants_open`。
"""
from __future__ import annotations

import hashlib

from app.ai_auto.decider import AiTradeDecider, TradeDecision
from app.ai_auto.discipline import preset_table
from app.ai_auto.open_funnel import LAYERS, classify_block
from app.ai_auto.prompts import build_decision_prompt
from app.ai_auto.session import AiTradeSession
from app.ai_auto.trend_runtime import TrendRuntime
from app.ai_auto.trend_verdict import TrendVerdictRule, apply_trend_bias_gate
from app.config import Settings
from app.db.session import dispose_engine, init_db
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.okx.accounts import AccountCredentials
from app.okx.types import BAR_SECONDS
from app.paper.strategy_factory import PaperStrategySpec
from app.strategies.base import BaseStrategy, StrategyMeta


class _Stub(BaseStrategy):
    meta = StrategyMeta(key="_stub_trend", name="Stub", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


def _settings(**over) -> Settings:
    return Settings(_env_file=None, **{**preset_table("balanced"), **over})


def _session(settings: Settings) -> AiTradeSession:
    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(), ct_val=0.01, lot_sz=0.01,
        min_sz=0.01, settings=settings, bar_seconds=BAR_SECONDS["15m"])
    decider = AiTradeDecider(object(), "sys", context_refresh_rounds=50, settings=settings)
    return AiTradeSession(
        "aitrade_trend", 1, [PaperStrategySpec(1, "S", "atomic", _Stub({}), {})],
        "BTC-USDT-SWAP", "15m", executor, decider, priming=False, priming_bars=10,
        context_refresh_rounds=50, discipline_preset="balanced")


# ============================ S-5：总闸关 ⇒ 逐字节回退 ============================
async def test_master_switch_off_reverts_the_whole_chain():
    """S-5：`ai_trade_trend_verdict_enabled=False` ⇒ payload 无键、prompt sha256 与合入前相同、
    `TrendRuntime` 恒不装配（`stats_wire()` 为 None）。"""
    await init_db()
    try:
        session = _session(_settings(ai_trade_trend_verdict_enabled=False))
        assert session._trend_rule.enabled is False
        payload = {"round": 1, "barTs": 1, "lastPx": 100.0, "signalBoard": []}
        before = hashlib.sha256(build_decision_prompt(payload).encode()).hexdigest()
        session._trend.note_verdict(None)
        assert session._trend.stats_wire() is None
        assert hashlib.sha256(build_decision_prompt(payload).encode()).hexdigest() == before
        # 门禁在未装配时对**任何**动作原对象直通。
        decision = TradeDecision(action="open_short", size_fraction=0.5, confidence=0.1,
                                 reason="x")
        out = apply_trend_bias_gate(decision, None, session._trend_rule, position_step=0.25)
        assert out.decision is decision and out.reason is None
    finally:
        await dispose_engine()


async def test_master_switch_on_arms_the_rule_from_the_derived_settings_copy():
    """规则取自 `executor.settings`——那份 `_resolve_discipline` / `_resolve_trend` 之后的派生
    副本，与 `_apply_consensus_gate` 读的是同一个对象（读进程级单例会拿到死字母阈值）。"""
    await init_db()
    try:
        session = _session(_settings(ai_trade_trend_counter_min_confidence=0.8))
        assert session._trend_rule.enabled is True
        assert session._trend_rule.counter_min_confidence == 0.8
        assert session._trend.rule is session._trend_rule
    finally:
        await dispose_engine()


# ============================ S-1：payload 装配顺序 ============================
async def test_payload_injects_trend_after_tactical_context():
    """S-1：`trendVerdict` 的 `conflict` 读得到 `tacticalContext` ⇒ 它必须排在 wake 层之后。

    判据不看行号看**产物**：把战术背离放进 payload，产出的 `conflict` 必须含 tactical 那一格。
    排在 `_wake.inject` 之前，这个键恒为 `"none"` 且不报任何错（RK-6）。
    """
    await init_db()
    try:
        from app.ai_auto.trend_verdict import TrendInputs, build_trend_verdict

        session = _session(_settings())
        verdict = build_trend_verdict(TrendInputs(
            execution_bar="15m",
            regime={"primary": "trend_down", "confidence": 0.9},
            digest={"priceAction": {"emaFast": 99.0, "emaSlow": 100.0, "emaSlopePct": -0.9,
                                    "changePct": -3.0, "rangePos": 0.1,
                                    "consecUp": 0, "consecDown": 4},
                    "factors": {"macdHist": {"value": -1.0}, "rocPct": {"value": -4.0}}},
            multi_timeframe=None,
            tactical={"alignment": "diverged"}, as_of_ts=1), session._trend_rule)
        assert verdict["conflict"] == "tactical_conflict"
        assert verdict["tacticalAlignment"] == "diverged"
    finally:
        await dispose_engine()


# ============================ S-2 / S-3 / S-4：门禁插点与漏斗闭合 ============================
def test_trend_bias_layer_sits_before_consensus_in_the_funnel():
    """S-4 + 层序：`counter_trend_*` 归 `trendBias`，且该层排在 `consensus` **之前**。

    `LAYERS` 的顺序即漏斗层序，必须与 `session._run_decision_round` 的调用序逐字同构。
    """
    assert classify_block("counter_trend_confidence(score=-0.61,conf=0.40/0.65)") == "trendBias"
    assert classify_block("counter_trend_size(score=-0.32)") == "trendBias"
    assert LAYERS.index("trendBias") < LAYERS.index("consensus")
    assert LAYERS.index("drawdownBrake") < LAYERS.index("trendBias")


def test_blocked_round_is_already_hold_when_consensus_sees_it():
    """S-2：被基调闸拦下的轮此刻已是 hold ⇒ 共识门禁首行直通 exempt ⇒ 两条放宽通路自动不生效。

    这是「基调闸排在共识之前」带来的**免费护栏**。
    """
    from app.ai_auto.signals import Consensus, ConsensusRule, resolve_consensus_gate

    rule = TrendVerdictRule(enabled=True)
    verdict = {"bar": "15m", "bias": "long", "direction": "up", "strength": "strong",
               "score": 0.4, "execScore": 0.7, "conflict": "none", "degraded": False,
               "verdictConfidence": 0.7}
    out = apply_trend_bias_gate(
        TradeDecision(action="open_short", size_fraction=0.5, confidence=0.2, reason="x"),
        verdict, rule, position_step=0.25)
    assert out.decision.action == "hold" and out.reason.startswith("counter_trend_confidence")
    board = Consensus(long_votes=3, short_votes=0, flat_votes=0, total=3,
                      net_stance="long", agreement=1.0)
    downstream = resolve_consensus_gate(out.decision, board, ConsensusRule(min_consensus=2),
                                        position_step=0.25, used_contrarian=0,
                                        bonus_votes=1, opposing_allowance=1)
    assert downstream.lane == "exempt" and downstream.decision is out.decision


# ============================ S-7：开仓意愿的分母 ============================
def test_intent_denominator_reuses_the_open_funnel_criterion():
    """S-7（评审 RP1-1）：`low_confidence` 轮计进 `unknown`；真实观望轮**不进任何一格**。

    `low_confidence` 此刻 `action` 已被 `_map_decision` 洗成 `hold`（实测占开仓意愿 21%），
    照「只看 action」实现，`intents` 会退化成轮数计数器。
    """
    runtime = TrendRuntime(TrendVerdictRule(enabled=True))
    verdict = {"bar": "15m", "bias": "short", "direction": "down", "strength": "strong",
               "score": -0.4, "execScore": -0.7, "conflict": "none", "degraded": False}
    runtime.note_verdict(verdict)
    runtime.note_intent("open_short", None, verdict)     # 顺势
    runtime.note_intent("open_long", None, verdict)      # 逆势
    runtime.note_intent("hold", "low_confidence", verdict)   # 方向不可辨的开仓意愿
    runtime.note_intent("hold", None, verdict)           # 真实观望：**不进任何一格**
    runtime.note_intent("close", None, verdict)          # 平仓：同样不进
    runtime.note_intent("hold", "llm_error", verdict)    # LLM 故障：不是开仓意愿
    wire = runtime.stats_wire()
    assert wire["intents"] == {"aligned": 1, "counter": 1, "unknown": 1}


def test_moderate_verdict_intents_are_still_bucketed_by_side():
    """`moderate` 基调的开仓意愿**不得**被冲进 `unknown`。

    强度只是轴 D 三条通路的**触发条件**；顺势 / 逆势这个**关系**在中等强度上一样成立。把强度
    并进 `alignment_of`，卡面第 2 行（开仓意愿）与第 4 行（顺势逆势成绩）会把大量本可归属的
    样本记成「无基调 / 归属不明」——而 §7.7-3 的产品验收恰恰要在那两行上读出「空单意愿到底
    有没有出现」。`unknown` 只承接「方向不可辨」与「基调不可用」两件事。
    """
    runtime = TrendRuntime(TrendVerdictRule(enabled=True))
    verdict = {"bar": "15m", "bias": "short", "direction": "down", "strength": "moderate",
               "score": -0.3, "execScore": -0.4, "conflict": "none", "degraded": False}
    runtime.note_verdict(verdict)
    runtime.note_intent("open_short", None, verdict)
    runtime.note_intent("open_long", None, verdict)
    assert runtime.stats_wire()["intents"] == {"aligned": 1, "counter": 1, "unknown": 0}


# ============================ S-6b / S-6c：alignment 的跨轮认领（评审 RP0-5）============================
class _Resting:
    """`executor.resting` 的鸭子类型替身（只需 `intent` 与 `cl_ord_id` 两个属性）。"""

    def __init__(self, cl_ord_id: str) -> None:
        self.intent = "open"
        self.cl_ord_id = cl_ord_id


def _armed_runtime() -> TrendRuntime:
    runtime = TrendRuntime(TrendVerdictRule(enabled=True), min_samples=2)
    runtime.note_verdict({"bar": "15m", "bias": "short", "direction": "down",
                          "strength": "strong", "score": -0.4, "execScore": -0.7,
                          "conflict": "none", "degraded": False})
    return runtime


def test_alignment_is_frozen_at_submit_and_survives_across_rounds():
    """S-6b（评审 RP0-5 的回归护栏，**跨轮**）。

    第 3 轮基调偏空、开出一笔顺势空单（限价，未成交）；第 4 轮基调翻多；第 5 轮该挂单成交；
    第 40 轮平仓结算。断言 `filled.aligned == 1`、`netPnlUsd.aligned` 收到那笔净额——取的是
    **第 3 轮定格**的 alignment，不是成交轮 / 结算轮的基调。

    照 v1 的 `note_fill(side, alignment, pnl)` 实现（只能读当轮基调），本用例必红，而单轮
    用例恒绿——这正是它存在的理由。
    """
    runtime = _armed_runtime()
    # 第 3 轮：顺势空单发出。两步姿态：先置轮级槽（`_execute_decision` **之前**），
    # 再按 clOrdId 冻结进跨轮认领表（下单之后）。
    runtime.note_alignment("aligned")
    runtime.claim_alignment(_Resting("cl-3"))
    runtime.end_round()
    # 第 4 轮：基调翻多（若归因读当轮基调，下面那笔成交会被记到 counter 一侧）。
    runtime.note_verdict({"bar": "15m", "bias": "long", "direction": "up", "strength": "strong",
                          "score": 0.4, "execScore": 0.7, "conflict": "none", "degraded": False})
    runtime.end_round()
    # 第 5 轮：挂单成交（跨轮到达，按 clOrdId 认领）。
    runtime.note_fill({"clOrdId": "cl-3", "side": "short"})
    runtime.end_round()
    # 第 40 轮：平仓结算，净额回流到**开仓那一刻**的那一侧。
    runtime.note_settled(41.2)
    wire = runtime.stats_wire()
    assert wire["filled"] == {"aligned": 1, "counter": 0, "unknown": 0}
    assert wire["netPnlUsd"]["aligned"] == 41.2
    assert wire["netPnlUsd"]["counter"] == 0.0


def test_unclaimable_fill_goes_to_unknown_and_never_leaks_into_a_side():
    """S-6c：没有 `clOrdId`、也不属于任何本轮槽位的成交（接管仓位 / 离线平仓）⇒ `unknown`。"""
    runtime = _armed_runtime()
    runtime.note_fill({"side": "long"})
    runtime.note_settled(-8.7)
    wire = runtime.stats_wire()
    assert wire["filled"] == {"aligned": 0, "counter": 0, "unknown": 1}
    assert wire["netPnlUsd"]["unknown"] == -8.7


def test_market_order_falls_back_to_the_round_slot_then_is_cleared():
    """市价单没有 `resting` ⇒ 走轮级槽；轮末必须清零（一次被否的意图绝不改绑到下一笔成交）。"""
    runtime = _armed_runtime()
    runtime.note_alignment("aligned")
    runtime.claim_alignment(None)          # 市价单无挂单 ⇒ 空转，轮级槽已经够用
    runtime.note_fill({"side": "short"})
    runtime.end_round()
    runtime.note_fill({"side": "long"})          # 下一轮的孤儿成交
    wire = runtime.stats_wire()
    assert wire["filled"] == {"aligned": 1, "counter": 0, "unknown": 1}


def test_counter_pass_is_not_counted_as_a_block():
    """`passed_counter` **不进 `blocked`**（镜像「`sizing_zeroed` 不是一道闸」的成文纪律）。"""
    runtime = _armed_runtime()
    rule = TrendVerdictRule(enabled=True)
    verdict = runtime.verdict
    passed = apply_trend_bias_gate(
        TradeDecision(action="open_long", size_fraction=0.5, confidence=0.9, reason="x"),
        verdict, rule, position_step=0.25)
    runtime.note_gate(passed)
    blocked = apply_trend_bias_gate(
        TradeDecision(action="open_long", size_fraction=0.5, confidence=0.1, reason="x"),
        verdict, rule, position_step=0.25)
    runtime.note_gate(blocked)
    wire = runtime.stats_wire()
    assert wire["counterPassed"] == 1
    assert wire["counterBlocked"] == {"confidence": 1, "size": 0}


# ============================ S-8：两条统计通道同源 ============================
async def test_status_and_ai_stats_share_the_same_wire():
    """S-8（评审 RP1-2）：`_status_payload()["trendVerdict"]` 与 `aiStats.trendVerdict` 同形。

    只落 `aiStats` 会让实时页那张卡恒为空——`session.py` 上方注释已白纸黑字写着「后端算了、
    类型补了、没有渲染落点 = 死数据」。
    """
    await init_db()
    try:
        session = _session(_settings())
        assert session._status_payload()["trendVerdict"] is None    # 零样本 ⇒ None
        session._trend.note_verdict({"bar": "15m", "bias": "short", "direction": "down",
                                     "strength": "strong", "score": -0.4, "execScore": -0.7,
                                     "conflict": "none", "degraded": False})
        live = session._status_payload()["trendVerdict"]
        stored = session._trend.merge_into({})["trendVerdict"]
        assert live == stored
        assert live["bar"] == "15m" and live["verdictRounds"]["down"] == 1
    finally:
        await dispose_engine()


def test_bonus_lanes_are_attributable_per_guardrail():
    """RK-1 缓解④ 的读点：两条放宽通路各为哪一侧点亮过几次、被哪条护栏挡下过几次，可查。"""
    runtime = _armed_runtime()
    runtime.note_bonus("applied", 1)
    runtime.note_bonus("blocked_conflict", 0)
    runtime.note_bonus("blocked_no_native_vote", 0)
    runtime.note_bonus("blocked_not_aligned", 0)
    runtime.note_bonus("none", 0)                # 总闸关 / 无基调：**什么都不记**
    lanes = runtime.stats_wire()["bonusLanes"]
    assert lanes == {"voteApplied": 1, "allowanceApplied": 1, "blockedNoNativeVote": 1,
                     "blockedConflict": 1, "blockedNotAligned": 1}


def test_round_slot_is_set_before_execution_so_market_fills_are_attributed():
    """实施期发现 F-7 的回归护栏：**市价单的成交事件在 `_execute_decision` 内部就发出了**。

    只有一个「下单之后才调」的入口（spec §3.7.1 的 `note_submitted(clOrdId, alignment)`），
    轮级槽在成交那一刻还是空的 ⇒ **每一笔市价开仓腿都落进 `unknown`**，而卡面照样有数、
    不报任何错。故本模块拆成 `note_alignment`（下单前）+ `claim_alignment`（下单后）两步，
    与 `AttributionLedger.note_estimate` / `claim_estimate` 逐字同构。
    """
    runtime = _armed_runtime()
    runtime.note_alignment("counter")       # ← `_execute_decision` **之前**
    runtime.note_fill({"side": "long"})     # ← `_dispatch` 在 `_execute_decision` **内部**发出
    runtime.claim_alignment(None)           # ← 返回之后（市价单无挂单，空转）
    assert runtime.stats_wire()["filled"] == {"aligned": 0, "counter": 1, "unknown": 0}


def test_non_open_rounds_never_set_the_round_slot():
    """`neutral`（平仓 / hold / 基调不可用）**不置位** ⇒ 那一轮的成交本就该落进 `unknown`。"""
    runtime = _armed_runtime()
    runtime.note_alignment("neutral")
    runtime.note_fill({"side": "long"})
    assert runtime.stats_wire()["filled"]["unknown"] == 1


# ============================ 调用序的源码门禁（S-1 / 实施期发现 F-7）============================
def test_session_call_order_is_frozen_in_source():
    """S-1 与 F-7 守的都是**一行代码的位置**，而位置挪了产物用例不会红。

    - S-1：`trendVerdict` 排在 `_wake.inject` **之前** ⇒ `conflict` 恒 `"none"`、战术背离永不
      生效，**且不报任何错**（RK-6）。上面那条 S-1 用例断言的是纯核心读得到 `tactical`，
      把装配点上移一行它照样绿。
    - F-7：`note_alignment` 排在 `_execute_decision` **之后** ⇒ 市价开仓腿的成交事件在它内部
      就已经由 `_dispatch` 发出，轮级槽那时还是空的 ⇒ **每一笔市价开仓腿落进 `unknown`**，
      卡面照样有数、不报任何错，而 S-6b（限价跨轮）走的正好是另一条路径、恒绿。

    故这两条只能由源码顺序冻结（`inspect.getsource` 的源码门禁在本仓库已有十余处先例）。
    """
    import inspect

    payload_src = inspect.getsource(AiTradeSession._build_decision_payload)
    wake = payload_src.index("self._wake.inject(")
    trend = payload_src.index("build_trend_verdict(")
    experience = payload_src.index("self._experience.inject_round(")
    assert wake < trend < experience, "trendVerdict 必须在 wake 之后、经验层之前装配"

    round_src = inspect.getsource(AiTradeSession._run_decision_round)
    brake = round_src.index("self._sizing.gate(")
    trend_gate = round_src.index("self._apply_trend_bias_gate(")
    consensus = round_src.index("self._apply_consensus_gate(")
    assert brake < trend_gate < consensus, "基调闸必须排在刹车之后、共识之前（漏斗层序同构）"

    note = round_src.index("self._trend.note_alignment(")
    execute = round_src.index("await self._execute_decision(")
    claim = round_src.index("self._trend.claim_alignment(")
    assert note < execute < claim, "alignment 必须在下单前置轮级槽、下单后才冻进认领表"
