"""交易意图固定公开投影；内部凭据指纹与请求上下文不可出 wire。"""

import json
import time

from .errors import TradingError

_PREVIEW_FIELDS = (
    "schemaVersion",
    "instId",
    "direction",
    "posSide",
    "tdMode",
    "ordType",
    "sizeContracts",
    "price",
    "referencePrice",
    "notionalEstimateUsdt",
    "marginEstimateUsdt",
    "leverageObserved",
    "tpTriggerPx",
    "slTriggerPx",
    "warnings",
    "observedTs",
    "side",
    "reduceOnly",
    "positionObservedSz",
    "positionIdentity",
    "accountMode",
    "acctLv",
    "posMode",
    "instrumentConstraints",
    "maxOpenContracts",
    "protectionStatus",
    "targetOrdId",
    "kind",
    "targetState",
)
_RESULT_FIELDS = (
    "ordId",
    "clOrdId",
    "exchangeState",
    "accFillSz",
    "avgPx",
    "lastCheckedTs",
    "protectionStatus",
    "reconcileError",
    "message",
)


def _project_preview(preview):
    result = {key: preview.get(key) for key in _PREVIEW_FIELDS}
    for key, fields in (
        (
            "positionIdentity",
            ("posId", "exchangeCTimeMs", "netSign", "mgnMode"),
        ),
        (
            "instrumentConstraints",
            ("ctVal", "ctValCcy", "lotSz", "minSz", "tickSz"),
        ),
    ):
        if isinstance(result[key], dict):
            result[key] = {field: result[key].get(field) for field in fields}
    return result


def intent_view(row):
    """将数据库真值转换成卡片，绝不把 accepted 解释为成交。"""
    from ..config import get_settings
    from .admission import is_ready

    preview = json.loads(row.preview_json)
    result = json.loads(row.result_json) if row.result_json else None
    settings = get_settings()
    can_execute = (
        row.status == "prepared"
        and row.expires_ts > int(time.time())
        and getattr(settings, "agent_trading_enabled", False)
        and getattr(settings, "agent_trading_execution_enabled", False)
        and settings.live_trading_enabled
        and is_ready()
    )
    return {
        "schemaVersion": 1,
        "id": row.id,
        "conversationId": row.conversation_id,
        "version": row.version,
        "action": row.action,
        "status": row.status,
        "account": {
            "id": row.account_id,
            "label": preview.get("accountLabel"),
            "environment": row.environment,
        },
        "bindingVersion": row.binding_version,
        "createdTs": row.created_ts,
        "expiresTs": row.expires_ts,
        "previewHash": row.preview_hash,
        "preview": _project_preview(preview),
        "result": {key: result.get(key) for key in _RESULT_FIELDS}
        if result is not None
        else None,
        "error": TradingError(row.error_code).as_dict()["error"]
        if row.error_code
        else None,
        "canExecute": bool(can_execute),
    }


async def current_intent_view(row, session_factory):
    """卡片资格额外核实当前绑定和工具开关；执行入口仍独立复核。"""
    from .binding import validate_call
    from .schemas import AgentCallContext
    from ..db.repositories import get_tool_states

    view = intent_view(row)
    if not view["canExecute"]:
        return view
    call = AgentCallContext(
        conversation_id=row.conversation_id,
        client_message_id=row.client_message_id,
        account_id=row.account_id,
        environment=row.environment,
        binding_version=row.binding_version,
        account_mode="live",
        credential_fingerprint=row.credential_fingerprint,
    )
    try:
        await validate_call(call, session_factory)
        async with session_factory() as session:
            states = await get_tool_states(session)
        tool = (
            "prepare_cancel_order"
            if row.action == "cancel"
            else "prepare_trade"
        )
        view["canExecute"] = states.get(tool, True)
    except TradingError:
        view["canExecute"] = False
    return view
