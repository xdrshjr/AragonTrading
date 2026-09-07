"""由当前会话事实生成统一诊断、模型输入与公开快照。"""
from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import asdict

from .decision_memory import RoundDecisionRecord, build_decision_facts
from .open_funnel import LAYER_LABELS, classify_block

_EXECUTION_MESSAGES = {
    "below_min_size": "订单数量低于交易所最小要求",
    "existing_order_kept": "保留原有限价委托，未新增订单",
    "position_not_flat": "已有持仓，暂停新增开仓",
    "same_side_position": "已有同向持仓",
    "exchange_rejected": "交易所明确拒绝开仓委托",
    "insufficient_balance": "交易所拒绝：可用保证金不足",
    "attempt_persist_failed": "订单身份未能保存，暂不提交开仓",
    "pre_submit_context_changed": "行情或持仓条件已变化，等待重新研判",
    "facts_unavailable": "当前事实不完整，暂不提交开仓",
    "quota_exhausted": "本次运行的逆势开仓额度已用尽",
    "session_not_running": "会话已暂停或停止",
}


def pending_open_wire(session) -> dict | None:
    """仅披露未决委托的身份、状态和时间。"""
    attempts = getattr(session.executor, "open_attempts", None)
    if attempts is None:
        return None
    pending = attempts.runtime_payload().get("pendingOpenAttempt")
    if not pending:
        return None
    keys = ("attemptId", "clOrdId", "ordId", "phase", "ack",
            "createdTs", "updatedTs", "instId", "lane")
    return {key: pending.get(key) for key in keys}


def _diagnosis(session, pending: dict | None) -> dict:
    decision = session._last_decision or {}
    reason = decision.get("degradeReason")
    sizing = decision.get("sizingVerdict") or {}
    floor = sizing.get("floorVerdict")
    receipt = (decision.get("executed") or {}).get("executionReceipt") or {}
    if pending and pending.get("ack") == "accepted":
        code, message = "accepted_pending", "交易所已接收，等待成交核对"
    elif pending:
        code, message = "acceptance_unknown", "开仓回执待核实，暂停新增开仓"
    elif session._stale_degraded:
        code, message = "market_unavailable", "行情证据不可用"
    elif floor in ("solved_zero", "below_gear_floor"):
        code = "sizing_zeroed"
        message = ("风险约束下仓位求解为零，未新增开仓"
                   if floor == "solved_zero" else
                   "仓位求解低于允许档位，未新增开仓")
    elif reason:
        code = classify_block(reason)
        message = LAYER_LABELS.get(code, "当前条件未通过")
    elif receipt.get("status") in ("not_submitted", "rejected"):
        code = receipt.get("reason", "other")
        message = _EXECUTION_MESSAGES.get(code, "本轮未提交新开仓")
    elif session.executor.resting is not None:
        code, message = "resting_order", "已提交限价，等待成交"
    elif session.executor.projected != "flat":
        code, message = "position_managed", "持仓监控中"
    else:
        code, message = "model_hold", "正常研判，等待合规机会"
    return {"code": code, "message": message,
            "details": {"reason": reason or receipt.get("reason") or floor}}


def build_integrity_snapshot(session) -> dict:
    """REST、WS和持久化统计共用的只读服务端事实。"""
    pending = pending_open_wire(session)
    decider = session.decider
    return {
        "schemaVersion": 2, "asOfTs": int(time.time()),
        "runStartedTs": session.started_ts,
        "runtimeEpochId": session._discipline.funnel.runtime_epoch_id,
        "runtimeEpochStartedTs": session._runtime_epoch_started_ts,
        "sampleThroughRound": session.rounds_completed,
        "diagnosis": _diagnosis(session, pending),
        "rules": {"schemaVersion": 2,
                  "consensus": asdict(session._consensus_rule),
                  "baseRules": (asdict(session.base_rule)
                                if session.base_rule else None),
                  "risk": asdict(session.executor.risk)},
        "funnel": session._discipline.funnel_wire(),
        "entryQuota": session._entry_quota.summary(),
        "pendingOpen": pending,
        "economics": session._cost.economics_snapshot(),
        "settlementValidation": deepcopy(getattr(
            session.executor, "last_settlement_validation", None)),
        "memory": decider.memory_wire(),
    }


def build_session_facts(session, payload: dict) -> dict:
    """从同一会话冻结必要事实，不从模型历史叙述推导状态。"""
    position = payload.get("position")
    if position is not None:
        position = {**position, "positionEpoch": session._position_seq}
    return build_decision_facts({
        **payload, "runId": session.run_id, "instId": session.inst_id,
        "position": position,
        "bar": session.bar, "asOfTs": int(time.time()),
        "pendingOpen": pending_open_wire(session),
        "rules": {"schemaVersion": 2,
                  "consensus": asdict(session._consensus_rule),
                  "baseRules": {"config": (asdict(session.base_rule)
                                           if session.base_rule else None),
                                "band": payload.get("baseRules")},
                  "risk": asdict(session.executor.risk)},
        "entryQuota": session._entry_quota.summary(),
        "funnel": session._discipline.funnel_wire(),
        "economics": session._cost.economics_snapshot(),
        "costSnapshot": session._cost.round_cost_wire(),
        "settlementValidation": deepcopy(getattr(
            session.executor, "last_settlement_validation", None)),
        "evidenceHashes": payload.get("evidenceHashes"),
    })


def build_round_record(payload: dict, proposal, decision,
                       outcome: dict) -> RoundDecisionRecord:
    """将一轮提议及最终事实聚合给既有记忆写点。"""
    return RoundDecisionRecord(
        proposal=asdict(proposal) if proposal else None,
        final_decision=asdict(decision),
        facts=payload.get("decisionFacts") or {},
        execution_receipt=outcome.get("executionReceipt"),
        gate_veto=outcome.get("gateVeto"),
        sizing_verdict=outcome.get("sizingVerdict"),
        closed_bar_ts=payload.get("barTs"))


def execution_result_keys(receipt: dict | None) -> dict:
    """工具成功标志由接收事实决定，未知不能冒充未发请求。"""
    if not receipt:
        return {}
    status = receipt.get("status")
    return {"ok": status == "accepted",
            "skipped": status in ("not_submitted", "rejected"),
            "reason": receipt.get("reason"), "executionReceipt": receipt}
