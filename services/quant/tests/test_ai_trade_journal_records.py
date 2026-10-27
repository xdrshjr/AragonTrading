"""三档采集粒度的裁剪与入队记录的冻结性（ai-trade-audit-journal §2.2.3）。

最要紧的那条是 `test_enqueued_row_is_frozen_at_enqueue_time`：`session._round_trace` 是**原地
累积**的，把活对象的引用排进队列，落库时写下的会是**下一轮**的内容。序列化必须在入队时完成。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from app.ai_auto.journal.records import (
    CaptureLimits,
    DeciderSnapshot,
    EventJournalInput,
    JournalScope,
    RoundJournalInput,
    build_decision_row,
    build_event_row,
    build_fill_ref,
)


def _decision(**over):
    base = {"action": "open_long", "size_fraction": 0.5, "confidence": 0.72,
            "reason": "trend up", "ord_type": "market", "limit_px": None,
            "hold_exit_px": None, "exit_conviction": None, "exit_evidence": None}
    return SimpleNamespace(**{**base, **over})


def _snapshot(**over) -> DeciderSnapshot:
    base = {"analysis": "A" * 9000, "reflection": "R" * 9000, "thinking": "T" * 9000,
            "profile": {"sha256": "p" * 64, "systemPrompt": "SYS", "toolsJson": "[]",
                        "provider": "anthropic", "model": "claude-opus-5"},
            "request_shape": {"contextTurns": 6, "contextFirstRound": 3,
                              "summarySha256": "", "summaryText": "",
                              "finalUserSha256": "f" * 64, "finalUserText": "PROMPT",
                              "attempt": 2, "hintApplied": "llm_error",
                              "extraInstruction": False},
            "context_turn": {"assistant": {"action": "hold"}, "toolResult": "{}"},
            "usage": {"inputTokens": 8123, "outputTokens": 412},
            "latency_ms": 6120.0, "retries": 1, "depth": "deep"}
    return DeciderSnapshot(**{**base, **over})


def _input(**over) -> RoundJournalInput:
    base = {"round_no": 412, "ts": 1788000900, "wake": {"trigger": "interval"},
            "payload": {"lastPx": 100.0, "apiKey": "leak-me"},
            "decision": _decision(), "stage1_decision": _decision(action="open_short"),
            "executed": {"traded": True, "action": "open_long", "side": "long", "qty": 1.0},
            "degrade": None, "gate_degrade": None, "llm_degrade": None,
            "reflection_verdict": "confirm", "reflection_reason": None,
            "round_fills": ({"side": "long", "price": 100.0, "qty": 1.0, "fee": 0.1,
                             "pnl": 0.0, "clOrdId": "abc", "reason": "agent"},),
            "post_round_offset": 1}
    return RoundJournalInput(**{**base, **over})


def _scope(tier: str = "standard", **over) -> JournalScope:
    return JournalScope(group_id="grp-1", run_id=7, inst_id="BTC-USDT-SWAP", bar="15m",
                        limits=CaptureLimits(tier=tier, **over))


def test_minimal_tier_keeps_conclusions_and_drops_context():
    row = build_decision_row(_scope("minimal"), _input(), _snapshot(), {"schema": 1})
    cols = row["cols"]
    assert cols["action"] == "open_short" and cols["final_action"] == "open_long"
    assert cols["payload_json"] is None and cols["output_json"] is None
    assert cols["analysis_text"] is None and cols["reflection_text"] is None
    assert cols["prompt_text"] is None and cols["thinking_text"] is None
    # 门禁链是**结论类**字段，三档全落——`minimal` 档也要答得出「谁拦的」。
    assert cols["gates_json"] is not None
    assert cols["telemetry_json"] is not None


def test_standard_tier_keeps_context_but_not_full_text():
    row = build_decision_row(_scope("standard", text_max_chars=4000), _input(),
                             _snapshot(), None)
    cols = row["cols"]
    assert json.loads(cols["payload_json"])["apiKey"] == "[REDACTED]", "脱敏在写入时发生"
    assert json.loads(cols["output_json"])["action"] == "open_short", "模型**原始**入参"
    assert len(cols["analysis_text"]) == 4000
    assert cols["thinking_text"] is None and cols["prompt_text"] is None


def test_full_tier_raises_the_text_floor_and_stores_the_sent_prompt():
    row = build_decision_row(_scope("full", text_max_chars=4000), _input(), _snapshot(), None)
    cols = row["cols"]
    assert len(cols["analysis_text"]) == 9000, "full 档取 max(设置值, 20000)"
    assert cols["thinking_text"] is not None
    # 存的是**真正送出去**的那条文本（含重试后缀），不是按 payload 重算的那一条。
    assert cols["prompt_text"] == "PROMPT"


def test_payload_over_limit_is_truncated_and_flagged():
    scope = _scope("standard", payload_max_chars=40)
    row = build_decision_row(scope, _input(payload={"blob": "x" * 500}), _snapshot(), None)
    assert row["cols"]["payload_truncated"] is True
    assert len(row["cols"]["payload_json"]) == 40


def test_enqueued_row_is_frozen_at_enqueue_time():
    """入队后修改源 dict，落库内容**不变**（约束 1 的冻结性）。"""
    payload = {"lastPx": 1.0}
    row = build_decision_row(_scope(), _input(payload=payload), _snapshot(), None)
    payload["lastPx"] = 999.0
    assert json.loads(row["cols"]["payload_json"])["lastPx"] == 1.0


def test_prompt_sha_comes_from_the_outbound_shape_not_from_the_payload():
    row = build_decision_row(_scope(), _input(), _snapshot(), None)
    assert row["cols"]["prompt_sha256"] == "f" * 64


def test_context_chain_off_leaves_both_columns_null():
    scope = _scope("standard", context_chain=False)
    cols = build_decision_row(scope, _input(), _snapshot(), None)["cols"]
    assert cols["context_ref_json"] is None and cols["context_turn_json"] is None


def test_executed_column_admits_the_post_round_fill():
    """「`executed` 说没成交」不等于「这一轮没有成交」（评审 RP1-2）。

    利润保护在 `_post_round` 里平掉的那一笔**不在** `executed` 里。这一列若只取
    `executed.traded`，「成交与订单」页会整条漏掉那些「模型什么都没做、轮末被保护平了一笔」
    的轮次——而那正是本仓实测唯一为正的确定性离场通道。
    """
    legs = ({"side": "close", "price": 108.0, "qty": 1.0, "fee": 0.1, "pnl": 7.8,
             "reason": "profit_guard_peak"},)
    inp = _input(executed={"traded": False, "action": "hold"},
                 round_fills=legs, post_round_offset=0)
    cols = build_decision_row(_scope(), inp, _snapshot(), None)["cols"]
    assert cols["executed"] is True
    # 真的一条腿都没有时才是 False（那一轮确实什么都没发生）。
    empty = _input(executed={"traded": False, "action": "hold"}, round_fills=())
    assert build_decision_row(_scope(), empty, _snapshot(), None)["cols"]["executed"] is False


def test_fill_ref_marks_post_round_legs():
    """一轮里开仓成交 + `_post_round` 内保护平仓 ⇒ `legs` 恰好两条且第二条是 `post_round`。"""
    legs = ({"side": "long", "price": 100.0, "qty": 1.0, "fee": 0.1, "pnl": 0.0},
            {"side": "close", "price": 108.0, "qty": 1.0, "fee": 0.1, "pnl": 7.8,
             "reason": "profit_guard_peak"})
    ref = build_fill_ref(_input(round_fills=legs, post_round_offset=1))
    assert [leg["source"] for leg in ref["legs"]] == ["round", "post_round"]


def test_serialization_never_raises_on_unserializable_objects():
    """`json.dumps` 跑在决策轮的 `try` 内：喂一个不可序列化对象也必须出得来一行。"""
    class Boom:
        def __repr__(self) -> str:
            return "<boom>"

    row = build_decision_row(_scope(), _input(payload={"weird": Boom()}), _snapshot(), None)
    assert "<boom>" in row["cols"]["payload_json"]


def test_event_row_redacts_detail_and_clamps_columns():
    row = build_event_row(_scope(), EventJournalInput(
        category="lifecycle", code="GROUP_STARTED", level="info",
        message="x" * 900, detail={"apiKey": "s"}))
    assert len(row["cols"]["message"]) == 512
    assert json.loads(row["cols"]["detail_json"])["apiKey"] == "[REDACTED]"


def test_summary_profile_is_emitted_only_when_the_seed_is_present():
    assert build_decision_row(_scope(), _input(), _snapshot(), None)["summaryProfile"] is None
    snap = _snapshot(request_shape={**_snapshot().request_shape,
                                    "summarySha256": "s" * 64, "summaryText": "SEED"})
    record = build_decision_row(_scope(), _input(), snap, None)
    assert record["summaryProfile"]["kind"] == "summary"
    assert record["summaryProfile"]["systemPrompt"] == "SEED"
