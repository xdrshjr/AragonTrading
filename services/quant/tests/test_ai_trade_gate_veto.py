"""门禁否决注记的纯核心单测（ai-trade-directional-symmetry 轴 B）。

合入前，八道确定性门禁里**只有持仓周期闸**会把「我提议了什么、被谁否了」写回模型的对话上下文
（`horizon_runtime.build_gate_veto_note` 的第一行就把 `vetoedBy` 硬编码成 `"horizon"`）。其余七道
对模型完全静默 —— 模型下一轮读到的自己的历史是「上轮我决定观望」。

本文件守四件事：层名单一真源、反思层显式返回 `None`、总闸关时 horizon 轮逐字节回退、
以及三类**绝不能**产出注记的轮次（执行侧 closeBlocked / 第一阶段模型侧码 / 无门禁）。
纯离线（无网络、无 DB）。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.ai_auto.decider import TradeDecision
from app.ai_auto.gate_veto import build_gate_veto_note, veto_layer, veto_layer_label
from app.ai_auto.horizon_runtime import build_gate_veto_note as legacy_note
from app.ai_auto.open_funnel import LAYER_LABELS, classify_block

_PROPOSED = TradeDecision("open_short", 0.5, 0.72, "净立场偏多但结构已破位")

# 八道确定性门禁各一个真实降级码（取自各层纯核心的成文前缀）。
_LAYER_CODES = {
    "baseRuleMaBand": "base_rule_ma_band(pctB=0.42)",
    "horizon": "horizon_min_hold(120s)",
    "profitCooldown": "profit_cooldown(120s,long)",
    "turnover": "horizon_turnover_budget(0/3)",
    "exchangeBackstop": "horizon_backstop_uncovered",
    "drawdownBrake": "drawdown_brake_halt(dd=0.16)",
    "consensus": "low_consensus(0/1)",
    "edgeGate": "edge_below_cost(edge=8.1bps,cost=13.0bps,k=2.0)",
}


def _priority():
    return SimpleNamespace(declared="high", level="expedite", downgrade_reason=None,
                           wire={"intentStreak": 2, "intentLapses": 0})


def test_every_layer_gets_a_note_with_the_direction_preserved():
    """八层各一例：`vetoedBy` 与 `classify_block` 一致，且 `proposedAction` 保真带方向。"""
    for expected_layer, code in _LAYER_CODES.items():
        note = build_gate_veto_note(_PROPOSED, code)
        assert note is not None, code
        assert note["vetoedBy"] == expected_layer == classify_block(code)
        # ★ 本轴的全部价值：这个字段带着方向。
        assert note["proposedAction"] == "open_short"
        assert note["reason"] == code


def test_veto_layer_is_single_sourced():
    """层名只有一份真源：`veto_layer` 与 `classify_block` 对同一个码逐字相等（`reflection` 除外）。"""
    for code in _LAYER_CODES.values():
        assert veto_layer(code) == classify_block(code)
    assert veto_layer(None) is None


def test_trace_and_note_agree_on_llm_stage_degrades():
    """★ 注记与 `tool_call` 相位对**同一轮**必须同时有话说或同时闭嘴（评审 P1-7 的真正含义）。

    少传 `llm_degrade` 的那一侧会把第一阶段模型侧的码当成一次门禁否决：`low_confidence` 经
    `classify_block` 落进 `confidence`、其余 LLM 故障码落进 `other`，于是决策卡上会出现
    「模型提议的是『观望』，被『置信度门槛』降级」——一条**凭空捏造**的门禁事实，而此刻
    `proposedAction` 已被 `_map_decision` 洗成 hold、方向早就丢了。生产实测这条路径占开仓
    意愿的 21%，不是边角料。
    """
    hold = TradeDecision("hold", None, None, "low_confidence")
    for code in ("low_confidence", "no_tool_call", "missing_size", "reasoning_truncated",
                 "llm_error", "decision_timeout"):
        assert veto_layer(code, llm_degrade=code) is None, code
        assert build_gate_veto_note(hold, code, llm_degrade=code) is None, code
    # 反过来：确定性门禁的码两侧都必须有话说，且说的是同一层。
    for expected_layer, code in _LAYER_CODES.items():
        layer = veto_layer(code, llm_degrade="low_confidence")
        assert layer == expected_layer
        assert build_gate_veto_note(_PROPOSED, code,
                                    llm_degrade="low_confidence")["vetoedBy"] == layer


def test_layer_label_is_backend_supplied_chinese():
    """中文层名由后端下发（前端不复制），且与 `open_funnel.LAYER_LABELS` 同源。"""
    note = build_gate_veto_note(_PROPOSED, "low_consensus(0/1)", layer_label=True)
    assert note["layerLabel"] == LAYER_LABELS["consensus"] == "同向共识门槛"
    assert veto_layer_label("consensus") == "同向共识门槛"
    assert veto_layer_label(None) is None


def test_reflection_layer_returns_none():
    """反思否决另有 `build_reflection_note` 承载，两条注记同时出现会让模型读到重复事实。"""
    assert build_gate_veto_note(_PROPOSED, "reflection_abort(evidence)") is None
    assert veto_layer("reflection_abort(evidence)") is None


def test_horizon_note_is_byte_identical_to_legacy_when_switch_off():
    """总闸关 ⇒ horizon 码的注记与合入前 `horizon_runtime.build_gate_veto_note` 逐字节相等。

    总闸开时注记多一个 `layerLabel`，那是**有意的**行为变更（评审 P0-2c）——本用例故意加了
    「总闸关」这个限定，不加限定的原用例名在总闸开时必红。
    """
    priority = _priority()
    for code in ("horizon_min_hold(120s)", "horizon_exit_unconfirmed(1/2)",
                 "horizon_turnover_hysteresis(3bps)"):
        assert (build_gate_veto_note(_PROPOSED, code, priority=priority)
                == legacy_note(_PROPOSED, code, priority))
    # 总闸开：唯一的差别就是多出来的那一个键。
    on = build_gate_veto_note(_PROPOSED, "horizon_min_hold(120s)", priority=priority,
                              layer_label=True)
    off = legacy_note(_PROPOSED, "horizon_min_hold(120s)", priority)
    assert set(on) - set(off) == {"layerLabel"}
    assert {k: on[k] for k in off} == off


def test_no_note_when_no_deterministic_gate_fired():
    """`gate_degrade is None` ⇒ `None`；`gate_degrade == llm_degrade` ⇒ `None`。

    判据**不依赖任何对象身份**：`_apply_sizing` 本就会 replace 出新对象，
    `decision is not stage1_decision` 在任何开仓轮恒为真、什么都筛不出来（评审 P0-2a）。
    """
    assert build_gate_veto_note(_PROPOSED, None) is None
    assert build_gate_veto_note(None, "low_consensus(0/1)") is None
    assert build_gate_veto_note(_PROPOSED, "llm_error", llm_degrade="llm_error") is None


def test_close_blocked_never_becomes_a_gate_veto():
    """执行侧的 `closeBlocked` 绝不能被当成一次门禁否决（评审 P0-2a 的直接守卫）。

    `exit_order_slot_busy` 不在 `_DEGRADE_PREFIXES` 任何前缀里，会经 `classify_block` 落进
    `"other"`。真实调用点取的是**执行之前**的 `gate_degrade`（`None`），故注记恒为 `None`
    ——模型绝不会读到一条凭空捏造的「我被『其它』层拦下了」。
    """
    assert build_gate_veto_note(_PROPOSED, None) is None
    # 万一有人把合成后的码喂进来，它会落进 other —— 这条断言把那个形状钉在案上。
    assert classify_block("exit_order_slot_busy") == "other"


def test_llm_stage_degrades_never_produce_a_note():
    """第一阶段模型侧的码 ⇒ `None`（评审 P0-2b）。

    这些轮次 `_map_decision` 已经把 action 洗成 hold，写出去的 `proposedAction` 会**撒谎**
    ——而它带方向正是本轴宣称的全部价值。
    """
    hold = TradeDecision("hold", None, None, "low_confidence")
    for code in ("low_confidence", "no_tool_call", "missing_size", "reasoning_truncated"):
        assert build_gate_veto_note(hold, code, llm_degrade=code) is None


def test_consensus_note_tells_the_model_how_many_votes_it_was_short():
    """共识层的注记附票数与逆势名额——模型下一轮才知道「还差几票 / 还能不能走逆势通路」。"""
    wire = {"longVotes": 1, "shortVotes": 0, "minConsensus": 1,
            "contrarianAvailable": True, "contrarianRemaining": 3, "lane": "veto"}
    note = build_gate_veto_note(_PROPOSED, "low_consensus(0/1)", consensus_wire=wire)
    assert note["shortVotes"] == 0 and note["minConsensus"] == 1
    assert note["contrarianRemaining"] == 3
    # 共识快照只挂共识层：horizon 轮不得混进这几个键（否则 horizon 的逐字节回退当场失效）。
    horizon = build_gate_veto_note(_PROPOSED, "horizon_min_hold(120s)", consensus_wire=wire)
    assert "shortVotes" not in horizon
