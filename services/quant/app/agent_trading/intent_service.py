"""只读预览与持久意图入口；工具没有执行权限。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from uuid import uuid4

from ..config import get_settings
from ..db.agent_trade_repositories import AgentTradeRepository
from ..db.repositories import get_tool_states
from ..db.session import get_sessionmaker
from ..okx.trade_client import OKXTradeClient
from .binding import validate_call
from .errors import TradingError
from .preview import build_preview, load_facts
from .result_view import intent_view
from .schemas import CancelRequest, parse_request, parse_trade

PREVIEW_TTL_S = 60


def canonical_json(value):
    """稳定 JSON 同时用于 requestHash 与 previewHash。"""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def digest(value):
    """SHA-256 不包含会变化的结果状态。"""
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


async def ensure_prepare_enabled(tool, factory):
    """每次创建重新读工具开关，不能只信轮次开始时的注册快照。"""
    if not getattr(get_settings(), "agent_trading_enabled", False):
        raise TradingError("AGENT_TRADING_DISABLED")
    async with factory() as session:
        enabled = await get_tool_states(session)
    if enabled.get(tool) is False:
        raise TradingError("TOOL_DISABLED")


async def _prepare_context(call, request, factory):
    tool = (
        "prepare_cancel_order"
        if request["action"] == "cancel"
        else "prepare_trade"
    )
    await ensure_prepare_enabled(tool, factory)
    identity = await validate_call(call, factory)
    if call.account_mode != "live":
        raise TradingError("TRADING_MODE_REQUIRED")
    repo = AgentTradeRepository(factory)
    request_hash = digest(request)
    existing = await repo.find_request(
        call.conversation_id, call.client_message_id, request_hash
    )
    if existing and (
        existing.binding_version != call.binding_version
        or existing.account_id != call.account_id
        or existing.environment != call.environment
        or existing.credential_fingerprint != call.credential_fingerprint
    ):
        raise TradingError("BINDING_CHANGED")
    return identity, repo, request_hash, existing


async def _save_preview(call, request, preview, context):
    identity, repo, request_hash, _ = context
    await validate_call(call, repo.session_factory)
    preview["accountLabel"] = identity.label
    preview_hash = digest(
        {
            "preview": preview,
            "accountId": identity.account_id,
            "environment": identity.environment,
            "bindingVersion": call.binding_version,
            "credentialFingerprint": identity.credential_fingerprint,
        }
    )
    now = int(time.time())
    values = dict(
        id=str(uuid4()),
        conversation_id=call.conversation_id,
        client_message_id=call.client_message_id,
        tool_use_id=call.tool_use_id,
        account_id=identity.account_id,
        environment=identity.environment,
        binding_version=call.binding_version,
        credential_fingerprint=identity.credential_fingerprint,
        action=request["action"],
        status="prepared",
        version=1,
        request_hash=request_hash,
        preview_hash=preview_hash,
        request_json=canonical_json(request),
        preview_json=canonical_json(preview),
        created_ts=now,
        expires_ts=now + PREVIEW_TTL_S,
        updated_ts=now,
        target_ord_id=preview.get("targetOrdId"),
        _check_binding=True,
    )
    if preview.get("targetState") in {"filled", "canceled", "mmp_canceled"}:
        values.update(
            status="rejected",
            result_json=canonical_json(
                {
                    "ordId": preview["targetOrdId"],
                    "exchangeState": preview["targetState"],
                    "accFillSz": preview.get("targetAccFillSz"),
                    "avgPx": preview.get("targetAvgPx"),
                    "lastCheckedTs": now,
                }
            ),
        )
    return intent_view(await repo.create_intent(values))


async def prepare_trade(params, call, session_factory=None):
    """创建开/平仓预览，静态去重先于任何行情读取。"""
    from .admission import assert_auto_idle

    request = parse_trade(params)
    normalized = request.model_dump(exclude_none=True)
    factory = session_factory or get_sessionmaker()
    context = await _prepare_context(call, normalized, factory)
    if context[3]:
        return intent_view(context[3])
    await assert_auto_idle()
    facts = await load_facts(context[0], request)
    preview = build_preview(request, facts)
    return await _save_preview(call, normalized, preview, context)


async def _cancel_preview(identity, request):
    credentials = identity.credentials
    client = OKXTradeClient(
        credentials.api_key, credentials.secret_key, credentials.passphrase
    )
    try:
        order = await asyncio.wait_for(
            client.get_order(request.instId, ord_id=request.ordId), 15
        )
    except Exception as exc:
        raise TradingError("ACCOUNT_READ_FAILED") from exc
    finally:
        await client.aclose()
    if not order:
        raise TradingError("ORDER_NOT_FOUND")
    if (
        order.get("instId") != request.instId
        or order.get("ordId") != request.ordId
    ):
        raise TradingError("IDENTITY_MISMATCH")
    if order.get("algoId") or order.get("ordType") not in {
        "market",
        "limit",
        "post_only",
        "fok",
        "ioc",
        "optimal_limit_ioc",
    }:
        raise TradingError("UNSUPPORTED_ACTION")
    return _cancel_fields(order, request)


def _cancel_empty_fields():
    fields = {
        key: None
        for key in (
            "direction",
            "sizeContracts",
            "price",
            "referencePrice",
            "notionalEstimateUsdt",
            "marginEstimateUsdt",
            "leverageObserved",
            "tpTriggerPx",
            "slTriggerPx",
            "positionObservedSz",
            "positionIdentity",
            "acctLv",
            "posMode",
            "instrumentConstraints",
            "maxOpenContracts",
        )
    }
    return fields


def _cancel_fields(order, request):
    return {
        **_cancel_empty_fields(),
        "schemaVersion": 1,
        "instId": request.instId,
        "targetOrdId": request.ordId,
        "kind": "normal",
        "side": order.get("side"),
        "posSide": order.get("posSide"),
        "tdMode": order.get("tdMode"),
        "ordType": order.get("ordType"),
        "targetState": order.get("state"),
        "targetSnapshot": {
            key: order.get(key)
            for key in (
                "instId",
                "ordId",
                "side",
                "posSide",
                "tdMode",
                "ordType",
                "sz",
                "px",
            )
        },
        "targetAccFillSz": order.get("accFillSz") or None,
        "targetAvgPx": order.get("avgPx") or None,
        "reduceOnly": None,
        "accountMode": "live",
        "protectionStatus": "unverified",
        "observedTs": int(time.time()),
        "warnings": ["撤销受理不代表订单已撤销"],
        "payload": {"instId": request.instId, "ordId": request.ordId},
    }


async def prepare_cancel_order(params, call, session_factory=None):
    """只对绑定账户普通订单创建撤销预览，不调用写端点。"""
    from .admission import assert_auto_idle

    request = parse_request(CancelRequest, params)
    normalized = {"action": "cancel", **request.model_dump()}
    context = await _prepare_context(
        call, normalized, session_factory or get_sessionmaker()
    )
    if context[3]:
        return intent_view(context[3])
    await assert_auto_idle()
    preview = await _cancel_preview(context[0], request)
    return await _save_preview(call, normalized, preview, context)
