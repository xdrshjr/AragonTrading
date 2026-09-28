"""自动交易的证据投影；身份不确定时保留占位但不导出研判细节。"""

from __future__ import annotations

import time


def _decision(value):
    if not isinstance(value, dict):
        return None
    executed = value.get("executed") or {}
    reasons = list(value.get("gateReasons") or [])
    reasons.extend([executed.get("reason"), value.get("degradeReason")])
    return {
        "action": str(value.get("action") or "")[:32],
        "reason": str(value.get("reason") or "")[:512],
        "ts": value.get("ts"),
        "round": value.get("round"),
        "executed": {key: executed.get(key) for key in ("ok", "skipped")},
        "gateReasons": [
            str(item)[:128]
            for item in reasons[:10]
            if isinstance(item, str) and item
        ],
    }


def _position(value):
    if not isinstance(value, dict):
        return None
    return {
        key: value.get(key)
        for key in (
            "instId",
            "side",
            "posSide",
            "size",
            "sz",
            "avgPx",
            "unrealizedPnl",
        )
        if isinstance(value.get(key), (str, int, float, type(None)))
    }


def project_context(snapshot, identity, inst_id=None):
    """仅导出身份匹配的公开字段，永不复制 prompt/trajectory/原始思考。"""
    if not snapshot:
        return {"state": "idle", "members": [], "snapshotTs": int(time.time())}
    proof = snapshot.get("identity") or {}
    matched = (
        proof.get("accountId") == identity.account_id
        and proof.get("environment") == identity.environment
        and proof.get("credentialFingerprint")
        == identity.credential_fingerprint
    )
    result = {
        "groupId": snapshot.get("groupId"),
        "state": snapshot.get("state"),
        "snapshotTs": snapshot.get("snapshotTs") or int(time.time()),
        "lastDecision": None,
        "members": [],
    }
    if not matched:
        result["contextUnavailableReason"] = "CONTEXT_IDENTITY_UNAVAILABLE"
        return result
    result["lastDecision"] = _decision(snapshot.get("lastDecision"))
    for member in snapshot.get("members") or []:
        if inst_id and member.get("instId") != inst_id:
            continue
        decisions = member.get("decisions") or []
        result["members"].append(
            {
                **{
                    key: member.get(key)
                    for key in (
                        "instId",
                        "sessionId",
                        "runId",
                        "state",
                        "bar",
                        "lastRound",
                    )
                },
                "positionSummary": _position(member.get("position")),
                "lastDecision": _decision(decisions[-1]) if decisions else None,
            }
        )
    return result


async def get_context(identity, inst_id=None):
    """通过 manager 公共只读出口获取快照，不触发任何模型分析。"""
    from ..ai_auto.manager import get_ai_trade_manager
    from ..live_auto.manager import get_live_auto_manager

    snapshots = [
        get_ai_trade_manager().trading_context(),
        get_live_auto_manager().trading_context(),
    ]
    return {
        "source": "local_auto_trade",
        "groups": [
            project_context(snapshot, identity, inst_id)
            for snapshot in snapshots
            if snapshot
        ],
        "snapshotTs": int(time.time()),
    }
