"""轴 D 门禁用例（ai-trade-execution-trend-verdict §7.2）。

其中四条是**评审的回归护栏**——照 v1 的写法实现必红：

- **D-11（RP0-1）**：顺势追加票的判据不含本轮动作 ⇒ 逆势开仓会白拿一票。
- **D-12（RP0-2）**：D-1a 在它被指派去解决的断面（信号板一边倒）上恒不生效；出口只有 D-1b。
- **D-14（RP1-8）**：逆势闸比的是**模型自报**置信度，不是基调置信度（取错方向相反）。
- **D-5**：粗档位陷阱——`0.5×0.5=0.25` 被量化吸附回 `0.5`，承诺的强制降档当场失效且不报错。
"""
from __future__ import annotations

from dataclasses import replace

from app.ai_auto.decider import TradeDecision
from app.ai_auto.signals import Consensus, ConsensusRule, resolve_consensus_gate
from app.ai_auto.trend_verdict import (
    TrendVerdictRule,
    alignment_of,
    apply_trend_bias_gate,
    trend_bonus_lane,
    trend_consensus_bonus,
    trend_contrarian_allowance,
)

RULE = TrendVerdictRule(enabled=True)


def _verdict(*, bias="short", strength="strong", conflict="none", degraded=False,
             score=-0.32, exec_score=-0.61) -> dict:
    direction = {"long": "up", "short": "down", "neutral": "range"}[bias]
    return {"bar": "15m", "score": score, "execScore": exec_score,
            "direction": "unknown" if degraded else direction, "bias": bias,
            "strength": strength, "verdictConfidence": 0.59, "conflict": conflict,
            "bonusWithheld": conflict != "none", "degraded": degraded,
            "counterGate": {"armed": True, "minConfidence": 0.65, "sizeMult": 0.5}}


def _decision(action="open_short", size=0.5, confidence=0.8) -> TradeDecision:
    return TradeDecision(action=action, size_fraction=size, confidence=confidence, reason="x")


def _board(long_votes: int, short_votes: int) -> Consensus:
    total = long_votes + short_votes
    return Consensus(long_votes=long_votes, short_votes=short_votes, flat_votes=0,
                     total=total, net_stance="long" if long_votes > short_votes else "short",
                     agreement=1.0)


# ============================ D-1 … D-2：直通断面 ============================
def test_non_open_actions_pass_through_same_object():
    """D-1：`close` / `hold` / `cancel_order` ⇒ `exempt` 且返回**同一个对象**（`is` 相等）。"""
    for action in ("close", "hold", "cancel_order"):
        decision = _decision(action=action)
        out = apply_trend_bias_gate(decision, _verdict(bias="long"), RULE, position_step=0.25)
        assert out.lane == "exempt" and out.reason is None
        assert out.decision is decision


def test_gate_only_bites_when_verdict_is_strong():
    """D-2：`strength != "strong"` ⇒ 逆势轮同样直通（闸只在基调强时咬）。"""
    decision = _decision()
    out = apply_trend_bias_gate(decision, _verdict(bias="long", strength="moderate"),
                                RULE, position_step=0.25)
    assert out.lane == "exempt" and out.decision is decision


def test_moderate_verdict_still_has_an_alignment_even_though_no_lane_arms():
    """**强度不是 `alignment_of` 的判据**（spec §3.3.1 步骤 ④ / §3.3.2 共享前置各自单列了它）。

    把强度并进那个函数，一个「偏空·强度中」的轮次开出的空单会被归成「无基调」，统计卡第 2、4
    行（开仓意愿 / 顺势逆势成绩）就会把大量本可归属的样本冲进 `unknown` 格——而 §7.7-3 的产品
    验收恰恰要在那两行上读出「空单意愿到底有没有出现」。关系照常成立、三条通路照常不点亮，
    是本轮唯一正确的形态。
    """
    moderate = _verdict(bias="short", strength="moderate")
    assert alignment_of("open_short", moderate) == "aligned"
    assert alignment_of("open_long", moderate) == "counter"
    # 但三条通路一条都不点亮：触发条件仍是 `strength == "strong"`。
    assert trend_consensus_bonus("open_short", moderate, _board(0, 2), RULE) == 0
    assert trend_contrarian_allowance("open_short", moderate, RULE) == 0
    decision = _decision(action="open_long", confidence=0.1)   # 逆势且置信度远低于下界
    out = apply_trend_bias_gate(decision, moderate, RULE, position_step=0.25)
    assert out.lane == "exempt" and out.reason is None and out.decision is decision


def test_aligned_open_passes_through_unchanged():
    """顺势开仓一律直通（本层只收紧逆势那一侧，且**不发任何票**）。"""
    decision = _decision()
    out = apply_trend_bias_gate(decision, _verdict(bias="short"), RULE, position_step=0.25)
    assert out.lane == "aligned" and out.decision is decision


def test_disabled_counter_gate_passes_through():
    """只关 `counter_gate_enabled` ⇒ 基调仍在，但不再拦任何一笔（**收紧**通路关闭）。"""
    decision = _decision()
    out = apply_trend_bias_gate(decision, _verdict(bias="long"),
                                replace(RULE, counter_gate_enabled=False), position_step=0.25)
    assert out.lane == "exempt" and out.decision is decision


# ============================ D-3 … D-6：逆势闸 ============================
def test_counter_trend_below_confidence_floor_is_vetoed():
    """D-3：逆势 + 置信度低于下界 ⇒ `counter_trend_confidence(...)`，决策变 hold。"""
    out = apply_trend_bias_gate(_decision(confidence=0.4), _verdict(bias="long"),
                                RULE, position_step=0.25)
    assert out.lane == "veto"
    assert out.reason.startswith("counter_trend_confidence")
    assert out.decision.action == "hold"


def test_counter_trend_passing_confidence_is_forcibly_downsized():
    """D-4：置信度达标 ⇒ 仓位变为 `quantize(proposed × mult)` 且**严格小于**原提议。"""
    out = apply_trend_bias_gate(_decision(size=0.5, confidence=0.9), _verdict(bias="long"),
                                RULE, position_step=0.25)
    assert out.lane == "counter" and out.reason is None
    assert out.decision.size_fraction == 0.25 < 0.5
    assert out.wire["sizeBefore"] == 0.5 and out.wire["sizeAfter"] == 0.25


def test_coarse_step_absorbs_downsize_and_must_veto():
    """D-5 **粗档位陷阱**：`step=0.5`、`proposed=0.5`、`mult=0.5` ⇒ 量化吸附回 0.5 ⇒ 必须报
    `counter_trend_size` 而**不是**按原仓位放行。

    ②的三判据不得简化成 `size <= 0`——那正是 directional-symmetry 实施期发现的方案缺陷 I-1 的
    同款形状：承诺的「强制降档」当场失效**且不报任何错**。
    """
    out = apply_trend_bias_gate(_decision(size=0.5, confidence=0.9), _verdict(bias="long"),
                                RULE, position_step=0.5)
    assert out.lane == "veto" and out.reason.startswith("counter_trend_size")
    assert out.decision.action == "hold"


def test_fail_closed_on_unverifiable_inputs():
    """D-6：`confidence=None` / `NaN` / `size_fraction=None` 三种输入一律走否决分支。

    「无法验证」不等于「合规」。
    """
    for decision in (_decision(confidence=None), _decision(confidence=float("nan")),
                     _decision(size=None, confidence=0.9)):
        out = apply_trend_bias_gate(decision, _verdict(bias="long"), RULE, position_step=0.25)
        assert out.lane == "veto" and out.decision.action == "hold"


def test_counter_trend_gate_is_side_symmetric():
    """D-7 / I-4：verdict 与 action 同时镜像 ⇒ 两次 outcome 的 lane / 码前缀 / 仓位逐字对称。

    本函数体内**不允许出现任何 `action == "open_short"` 的字面判断**；多空的差异只经
    `alignment_of` 的一次比较产生。
    """
    a = apply_trend_bias_gate(_decision("open_short", confidence=0.9), _verdict(bias="long"),
                              RULE, position_step=0.25)
    b = apply_trend_bias_gate(_decision("open_long", confidence=0.9), _verdict(bias="short"),
                              RULE, position_step=0.25)
    assert a.lane == b.lane == "counter"
    assert a.decision.size_fraction == b.decision.size_fraction
    low_a = apply_trend_bias_gate(_decision("open_short", confidence=0.1), _verdict(bias="long"),
                                  RULE, position_step=0.25)
    low_b = apply_trend_bias_gate(_decision("open_long", confidence=0.1), _verdict(bias="short"),
                                  RULE, position_step=0.25)
    assert low_a.reason.split("(")[0] == low_b.reason.split("(")[0]


def test_counter_gate_reads_model_confidence_not_verdict_confidence():
    """D-14（评审 RP1-8）：判据是**模型自报**置信度；取 `verdictConfidence` 极性正好反了。"""
    strong_verdict = dict(_verdict(bias="long"), verdictConfidence=0.99)
    blocked = apply_trend_bias_gate(_decision(confidence=0.4), strong_verdict,
                                    RULE, position_step=0.25)
    assert blocked.lane == "veto"
    weak_verdict = dict(_verdict(bias="long"), verdictConfidence=0.1)
    passed = apply_trend_bias_gate(_decision(confidence=0.9), weak_verdict,
                                   RULE, position_step=0.25)
    assert passed.lane == "counter"
    # 两个 confidence 必须各有其名（共用一个键会让取错的那一方向相反）。
    assert passed.wire["modelConfidence"] == 0.9
    assert passed.wire["verdictConfidence"] == 0.1


# ============================ D-8 … D-13：两条顺势放宽通路 ============================
def test_bonus_needs_at_least_one_native_vote():
    """D-8：信号板该方向 0 票 ⇒ 恒返回 0（D-1a **不雪中送炭**）。"""
    assert trend_consensus_bonus("open_short", _verdict(bias="short"), _board(3, 0), RULE) == 0
    assert trend_bonus_lane("open_short", _verdict(bias="short"), _board(3, 0),
                            RULE) == "blocked_no_native_vote"


def test_bonus_requires_strong_and_no_conflict():
    """D-9：`moderate` ⇒ 0；`strong` + 1 票 + `conflict="none"` ⇒ 1；`htf_conflict` ⇒ **0**。"""
    board = _board(0, 1)
    assert trend_consensus_bonus("open_short", _verdict(strength="moderate"), board, RULE) == 0
    assert trend_consensus_bonus("open_short", _verdict(), board, RULE) == 1
    assert trend_consensus_bonus("open_short", _verdict(conflict="htf_conflict"),
                                 board, RULE) == 0
    assert trend_bonus_lane("open_short", _verdict(conflict="htf_conflict"), board,
                            RULE) == "blocked_conflict"


def test_bonus_and_allowance_require_the_action_itself():
    """D-11（评审 RP0-1 的回归护栏）：基调偏多 + 本轮开空 ⇒ **两个函数双双返回 0**。

    v1 的签名不含 `action`，本用例必红：`resolve_consensus_gate` 会把追加票加到
    `decision.action` 那一侧，于是「基调偏多、模型开空」这一轮空头方向会白拿一票——与本通路
    声称的「顺势才追加」正好相反，且不报任何错。
    """
    verdict = _verdict(bias="long")
    assert trend_consensus_bonus("open_short", verdict, _board(1, 1), RULE) == 0
    assert trend_contrarian_allowance("open_short", verdict, RULE) == 0
    assert trend_bonus_lane("open_short", verdict, _board(1, 1), RULE) == "blocked_not_aligned"
    # 反过来，顺势那一侧照常点亮（对称性：判据只经 `alignment_of` 一次比较产生方向差异）。
    assert trend_consensus_bonus("open_long", verdict, _board(1, 1), RULE) == 1
    assert trend_contrarian_allowance("open_long", verdict, RULE) == 1


def test_allowance_ignores_htf_conflict_but_not_tactical():
    """D-13：`tactical_conflict` / `both` ⇒ 0；`htf_conflict` ⇒ 1（与 D-1a 的判据**故意不同**）。

    理由是需求原话：判定级别是用户所选的执行周期，高周期只是调制项；而战术分歧说的是
    「此刻不是入场时机」，本通路放宽的恰恰是入场资格。
    """
    assert trend_contrarian_allowance("open_short", _verdict(conflict="htf_conflict"), RULE) == 1
    assert trend_contrarian_allowance("open_short", _verdict(conflict="tactical_conflict"),
                                      RULE) == 0
    assert trend_contrarian_allowance("open_short", _verdict(conflict="both"), RULE) == 0


def test_master_switches_are_independently_revertible():
    """§7.7-5：只关放宽的两把 ⇒ 两个函数各自归零，逆势闸照常工作。"""
    verdict = _verdict(bias="short")
    no_vote = replace(RULE, consensus_vote_enabled=False)
    no_allow = replace(RULE, contrarian_allowance_enabled=False)
    assert trend_consensus_bonus("open_short", verdict, _board(0, 1), no_vote) == 0
    assert trend_contrarian_allowance("open_short", verdict, no_vote) == 1
    assert trend_contrarian_allowance("open_short", verdict, no_allow) == 0
    assert trend_consensus_bonus("open_short", verdict, _board(0, 1), no_allow) == 1


def test_degraded_verdict_never_arms_any_lane():
    """基调不可用 ⇒ 三条通路全部空转（`alignment_of` 恒 `neutral`）。"""
    verdict = _verdict(degraded=True)
    assert alignment_of("open_short", verdict) == "neutral"
    assert trend_consensus_bonus("open_short", verdict, _board(0, 3), RULE) == 0
    assert trend_contrarian_allowance("open_short", verdict, RULE) == 0
    decision = _decision()
    assert apply_trend_bias_gate(decision, verdict, RULE, position_step=0.25).decision is decision


# ============================ D-10 / D-12：与共识门禁的接线 ============================
def test_consensus_gate_is_byte_identical_with_zero_relaxation():
    """D-10：两个放宽入参全 0 ⇒ 与合入前**逐字节相同**（放行轮返回同一对象，`is` 相等）。"""
    decision = _decision("open_long", size=0.5, confidence=0.8)
    rule = ConsensusRule(min_consensus=1)
    passed = resolve_consensus_gate(decision, _board(2, 0), rule,
                                    position_step=0.25, used_contrarian=0,
                                    bonus_votes=0, opposing_allowance=0)
    assert passed.decision is decision and passed.reason is None
    vetoed = resolve_consensus_gate(decision, _board(0, 2), ConsensusRule(min_consensus=2),
                                    position_step=0.25, used_contrarian=0,
                                    bonus_votes=0, opposing_allowance=0)
    assert vetoed.reason == "low_consensus(0/2)"
    assert vetoed.wire["effectiveVotes"] == vetoed.wire["votes"] == 0
    assert (vetoed.wire["effectiveMaxOpposingVotes"]
            == vetoed.wire["contrarianMaxOpposingVotes"] == 1)


def test_board_one_sided_short_is_the_target_cross_section():
    """D-12（评审 RP0-2 的目标断面）：板子 3 票看多 0 票看空、模型开空、强空基调同向。

    - 关掉 `contrarian_allowance` ⇒ 死在**反向票上限**（3 > 1）；
    - 开启它 ⇒ 仍被拦（3 > 1+1）——它只抬高 1，不是免检；
    - 把配置上限调到 2 ⇒ 走逆势通路放行且仓位被 ×0.5，全程 `contrarianEvidence` 与置信度
      下界照常生效。
    - 全程 `trend_consensus_bonus` 恒为 0（D-1a 在这个断面上被自己的护栏挡死）。
    """
    verdict = _verdict(bias="short")
    board = _board(3, 0)
    assert trend_consensus_bonus("open_short", verdict, board, RULE) == 0

    decision = TradeDecision(action="open_short", size_fraction=0.5, confidence=0.9,
                             reason="x", contrarian_evidence="逆势证据" * 20)
    base_rule = ConsensusRule(min_consensus=1, contrarian_enabled=True,
                              contrarian_max_opposing_votes=1)
    blocked = resolve_consensus_gate(decision, board, base_rule, position_step=0.25,
                                     used_contrarian=0, opposing_allowance=0)
    assert blocked.reason.startswith("low_consensus_opposed")

    still_blocked = resolve_consensus_gate(decision, board, base_rule, position_step=0.25,
                                           used_contrarian=0, opposing_allowance=1)
    assert still_blocked.reason.startswith("low_consensus_opposed")   # 3 > 1+1

    wide = replace(base_rule, contrarian_max_opposing_votes=2)
    passed = resolve_consensus_gate(decision, board, wide, position_step=0.25,
                                    used_contrarian=0, opposing_allowance=1)
    assert passed.lane == "contrarian" and passed.reason is None
    assert passed.decision.size_fraction == 0.25 < 0.5
    assert passed.wire["trendOpposingAllowance"] == 1
    assert passed.wire["effectiveMaxOpposingVotes"] == 3
    # 逆势通路的其余代价一条不减：证据写不够仍被拦。
    thin = replace(decision, contrarian_evidence="短")
    assert resolve_consensus_gate(thin, board, wide, position_step=0.25, used_contrarian=0,
                                  opposing_allowance=1).reason.startswith(
                                      "low_consensus_evidence")


def test_bonus_vote_only_solves_the_one_vote_short_case():
    """D-1a 的作用域**只有「差一票」**：`steady`（min=2）+ 已有 1 票 ⇒ 追加票把它补成 2。"""
    decision = _decision("open_short", size=0.5, confidence=0.8)
    rule = ConsensusRule(min_consensus=2)
    out = resolve_consensus_gate(decision, _board(0, 1), rule, position_step=0.25,
                                 used_contrarian=0, bonus_votes=1, bonus_lane="applied")
    assert out.lane == "consensus" and out.decision is decision
    assert out.wire["votes"] == 1 and out.wire["effectiveVotes"] == 2
    assert out.wire["trendBonusVotes"] == 1 and out.wire["trendBonusLane"] == "applied"
