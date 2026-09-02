"""逆势通路的强制降档改用档位语义（ai-trade-fill-truth-and-effective-controls 轴 D）。

门禁 `G-D1`~`G-D6`。其中 `G-D1` / `G-D5` **在合入前必须是红的**：今天它们都报否决。

本文件最要紧的一条是 `G-D6`——`signals` 与 `trend_verdict` 两侧对**同一张真值表**逐格相等。
那两处的降档段在合入前是**逐字同构的两份字节**，而 `session._run_decision_round` 先调
`_apply_trend_bias_gate`、再调 `_apply_consensus_gate`。只改下游那一把，逆势开仓会在
`openFunnel` 的 `trendBias` 那一格被拦死，而共识侧的断言照样会绿（评审 RV-P0-1 / 放行条件 C-1）。
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.ai_auto.decider import TradeDecision
from app.ai_auto.gear_resolution import downgrade_one_gear
from app.ai_auto.signals import Consensus, ConsensusRule, resolve_consensus_gate
from app.ai_auto.trend_verdict import TrendVerdictRule, apply_trend_bias_gate

_EVIDENCE = "逆势理由" * 20          # 远超 60 字符下界


def _consensus_rule(**over) -> ConsensusRule:
    base = {"min_consensus": 1, "contrarian_enabled": True, "contrarian_size_mult": 0.5,
            "contrarian_min_confidence": 0.65, "contrarian_max_opposing_votes": 1,
            "contrarian_min_evidence_chars": 60, "contrarian_max_per_session": 3,
            "gear_downgrade": True}
    base.update(over)
    return ConsensusRule(**base)


def _trend_rule(**over) -> TrendVerdictRule:
    base = {"enabled": True, "counter_gate_enabled": True, "counter_min_confidence": 0.65,
            "counter_size_mult": 0.5, "gear_downgrade": True}
    base.update(over)
    return TrendVerdictRule(**base)


def _decision(size: float) -> TradeDecision:
    return TradeDecision(action="open_long", size_fraction=size, confidence=0.72,
                         reason="逆势试探", contrarian_evidence=_EVIDENCE)


def _board() -> Consensus:
    """信号板一边倒地看空——正是「逆势开多」这条通路存在的理由。"""
    return Consensus(long_votes=0, short_votes=1, flat_votes=0, total=1,
                     agreement=0.0, net_stance="short")


_COUNTER_VERDICT = {"strength": "strong", "bias": "short", "score": -0.7,
                    "execScore": -0.7, "verdictConfidence": 0.8}


def _consensus_size(step: float, proposed: float, *, gear: bool):
    out = resolve_consensus_gate(_decision(proposed), _board(),
                                 _consensus_rule(gear_downgrade=gear),
                                 position_step=step, used_contrarian=0)
    return out.lane, out.decision.size_fraction, out.wire.get("downgradeLane")


def _trend_size(step: float, proposed: float, *, gear: bool):
    out = apply_trend_bias_gate(_decision(proposed), _COUNTER_VERDICT,
                                _trend_rule(gear_downgrade=gear), position_step=step)
    return out.lane, out.decision.size_fraction, out.wire.get("downgradeLane")


# ---------- G-D1（合入前必须红）：已在最低格即放行 ----------

def test_lowest_gear_is_treated_as_already_downgraded_and_passes():
    """`step=0.5` / `proposed=0.5` ⇒ 放行、`size == 0.5`、`downgradeLane == "floor_is_min"`。

    今天这里报 `low_consensus_size` 否决：`0.5 × 0.5 = 0.25` 被 `quantize_size_fraction` 的
    `max(1, …)` 吸附回 `0.5`，于是 `shrink_requested and size >= proposed` 成立。生产实测这条
    实现细节让整条逆势通路在 2373 个决策轮里**一次都没放行过**，而 `positionStepPct = 0.5` 占
    全库 147 条 run 的 88.4%——它不是一个边角断面，它是这套系统的默认世界。
    """
    assert _consensus_size(0.5, 0.5, gear=True) == ("contrarian", 0.5, "floor_is_min")


def test_gate_off_restores_todays_veto():
    """`G-D3`：总闸关 ⇒ 逐字回退今天的否决。"""
    lane, size, note = _consensus_size(0.5, 0.5, gear=False)
    assert lane == "veto" and size is None and note == "mult"


# ---------- G-D2：真的掉一格 ----------

def test_downgrade_moves_exactly_one_gear_not_a_multiplication():
    """`step=0.25` / `proposed=1.0` ⇒ `0.75`（掉一格），**不是** `0.5`（乘性）。"""
    assert _consensus_size(0.25, 1.0, gear=True) == ("contrarian", 0.75, "gear")
    assert _consensus_size(0.25, 1.0, gear=False)[1] == 0.5


# ---------- G-D4：§十三 Q2 的裁定冻结 ----------

def test_step_one_means_the_lowest_gear_is_full_size_so_it_must_not_pass():
    """`step = 1.0`（每次全仓）时最低格就是 100%——「已降档」等于「不降档」，退回今天的否决。

    这是 Q2 反方意见的落点：放行它等于把安全阀开成一张全仓单。
    """
    assert downgrade_one_gear(1.0, step=1.0, size_mult=0.5, gear_downgrade=True) == (1.0, "mult")
    lane, size, _ = _consensus_size(1.0, 1.0, gear=True)
    assert lane == "veto" and size is None


# ---------- 连续档位退回乘性 ----------

def test_continuous_ladder_falls_back_to_multiplication():
    """`step <= 0`（无阶梯）⇒ 没有「掉一格」这个概念，逐字回退乘性降档。"""
    assert downgrade_one_gear(0.5, step=0.0, size_mult=0.5, gear_downgrade=True) == (0.25, "mult")


def test_a_proposal_between_two_gears_resolves_the_same_way_quantize_does():
    """落在两格之间的提议值按「就近半格」归格——与 `quantize_size_fraction` 逐格一致。

    `0.37` 在 `[0.25, 0.5, 0.75, 1.0]` 上距最小格 0.12 < 半格 0.125 ⇒ 归第 0 格 ⇒ 已在最低格。
    这不是「猜一个格」：`quantize_size_fraction(0.37, 0.25)` 给出的同样是 `0.25`，两者必须同解，
    否则「降档前」与「降档后」会各自活在两套档位语义里。
    """
    assert downgrade_one_gear(0.37, step=0.25, size_mult=0.5,
                              gear_downgrade=True) == (0.25, "floor_is_min")


def test_a_proposal_far_outside_the_ladder_falls_back_to_multiplication():
    """真正落在阶梯之外（不该发生）⇒ 防御性退回今天的乘性行为，由调用方的判据兜底。"""
    size, lane = downgrade_one_gear(1.9, step=0.25, size_mult=0.5, gear_downgrade=True)
    assert lane == "mult" and size == 1.0


# ---------- G-D5（合入前必须红）：上游那把闸也修了 ----------

def test_trend_bias_gate_passes_the_lowest_gear_too():
    """`resolve_trend_gate` 与共识那一把是**逐字同构**的，而它**排在前面**。

    只改 `signals.py` 的话，逆势开仓会在 `openFunnel` 的 `trendBias` 那一格以
    `counter_trend_size` 结束，`contrarianPassed > 0` 这条验收**结构上不可达**——而共识侧的
    单测照样全绿。这正是评审 RV-P0-1 的形状：同形缺陷成对出现，数漏一个就修漏一个。
    """
    assert _trend_size(0.5, 0.5, gear=True) == ("counter", 0.5, "floor_is_min")


def test_trend_gate_off_restores_todays_veto():
    lane, size, note = _trend_size(0.5, 0.5, gear=False)
    assert lane == "veto" and size is None
    # 否决轮同样留痕：只看笔数答不出「是被吸附回原值还是本来就不该开」。
    assert note == "mult"


# ---------- G-D6：两侧对同一张真值表逐格相等 ----------

@pytest.mark.parametrize("step,proposed", [
    (0.5, 0.5), (0.5, 1.0), (0.25, 0.25), (0.25, 0.5), (0.25, 0.75), (0.25, 1.0),
    (0.1, 0.1), (0.1, 1.0), (0.05, 0.05), (1.0, 1.0), (0.0, 0.5),
])
@pytest.mark.parametrize("gear", [True, False])
def test_both_gates_agree_on_the_same_truth_table(step, proposed, gear):
    """构造性证明两处共用 `downgrade_one_gear`——杜绝第三份镜像。

    只比 `(size, downgradeLane)`：两把闸的 `lane` 词汇不同（`contrarian` vs `counter`），那是
    它们各自的归因命名，不是降档结果。
    """
    _, c_size, c_lane = _consensus_size(step, proposed, gear=gear)
    _, t_size, t_lane = _trend_size(step, proposed, gear=gear)
    assert (c_size, c_lane) == (t_size, t_lane)


# ---------- 不变式 2：只可能更宽，绝不放大仓位 ----------

@pytest.mark.parametrize("step", [0.5, 0.25, 0.1, 0.05, 1.0])
@pytest.mark.parametrize("proposed", [0.05, 0.1, 0.25, 0.5, 0.75, 1.0])
def test_the_gate_never_lets_a_contrarian_order_grow(step, proposed):
    """不变式 2 的施加点是**闸**，不是谓词。

    `downgrade_one_gear` 只回答「这次降档该降到哪」；当提议值本身不在阶梯上（例如 `step=0.5`
    而模型给了 `0.25`），乘性回退算出的 `0.5` 会**大于**提议值——今天与本轮都由调用方那条
    `size > proposed ⇒ veto` 拦下。所以要断言的是「放行的那些轮，仓位绝不大于提议值」，而不是
    去要求谓词自己永不越界（那会把一条本该报否决的路径悄悄改成放行）。
    """
    for lane, size, _ in (_consensus_size(step, proposed, gear=True),
                          _trend_size(step, proposed, gear=True)):
        if lane in ("contrarian", "counter"):
            assert size is not None and size <= proposed + 1e-9


def test_other_four_contrarian_criteria_are_untouched():
    """`R-6`：本轴只改降档那一条，其余四道判据**一条不减**。"""
    rule = _consensus_rule()
    weak = replace(_decision(0.5), confidence=0.5)               # 置信度不足
    assert resolve_consensus_gate(weak, _board(), rule, position_step=0.5,
                                  used_contrarian=0).lane == "veto"
    thin = replace(_decision(0.5), contrarian_evidence="太短")     # 逐字证据不足
    assert resolve_consensus_gate(thin, _board(), rule, position_step=0.5,
                                  used_contrarian=0).lane == "veto"
    # 会话配额是硬上限
    assert resolve_consensus_gate(_decision(0.5), _board(), rule, position_step=0.5,
                                  used_contrarian=3).lane == "veto"
