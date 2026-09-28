"""受管发送任务：CAS 后至多一次 POST，任何不确定结果保留占位。"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from ..ai_auto.admission import get_admission_lock
from ..config import get_settings
from ..db.agent_trade_repositories import AgentTradeRepository
from ..okx.order_builder import (ClosePositionInput, IntentOrderInput, SubmissionTrace)
from ..okx.private_client import OKXPrivateError
from ..okx.trade_client import OKXTradeClient, SendOptions
from . import admission
from .dispatch_guard import DispatchEvidence, DispatchGuard
from .errors import TradingError
from .schemas import AgentCallContext

logger = logging.getLogger(__name__)
_owners: dict[str, asyncio.Task] = {}
_admitted: dict[str, asyncio.Event] = {}


def has_owner(intent_id: str) -> bool:
    """核对器不能抢查活跃发送任务。"""
    task = _owners.get(intent_id)
    return task is not None and not task.done()


def _enabled() -> bool:
    settings = get_settings()
    return (admission.is_ready() and settings.agent_trading_enabled
            and settings.agent_trading_execution_enabled
            and settings.live_trading_enabled)


async def execute_intent(session_factory, conversation_id, intent_id, request):
    """HTTP 仅等待服务拥有的任务；调用方取消不会取消提交。"""
    repository = AgentTradeRepository(session_factory)
    row = await repository.get_intent(intent_id, conversation_id)
    if row is None:
        raise TradingError("INTENT_NOT_FOUND")
    if row.status != "prepared":
        return row
    task = _owners.get(intent_id)
    if task is None:
        _admitted[intent_id] = asyncio.Event()
        task = asyncio.create_task(_bounded_execute(repository, row, request))
        _owners[intent_id] = task
        task.add_done_callback(lambda done: _release(intent_id, done))
    waiter = asyncio.create_task(_admitted[intent_id].wait())
    try:
        done, _ = await asyncio.wait({waiter, task}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            return await asyncio.shield(task)
        return await repository.get_intent(intent_id, conversation_id)
    finally:
        waiter.cancel()


def _release(identity, task):
    if _owners.get(identity) is task:
        _owners.pop(identity, None)
        _admitted.pop(identity, None)
    if not task.cancelled():
        task.exception()  # 观察断线后仍在执行的任务异常。


async def _bounded_execute(repository, row, request):
    try:
        return await asyncio.wait_for(_execute(repository, row, request), timeout=25)
    except asyncio.TimeoutError:
        return await repository.get_intent(row.id)


async def shutdown() -> None:
    """关闭发送闸后取消受管任务，finally 保留持久未知态。"""
    admission.begin_shutdown()
    tasks = list(_owners.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _call_for(row):
    return AgentCallContext(
        conversation_id=row.conversation_id,
        client_message_id=row.client_message_id, tool_use_id=row.tool_use_id,
        account_id=row.account_id, environment=row.environment,
        binding_version=row.binding_version, account_mode="live",
        credential_fingerprint=row.credential_fingerprint)


async def _validate_first(repository, row, request):
    from .binding import validate_call
    from ..db.repositories import get_tool_states
    admission.ensure_ready()
    if not _enabled():
        raise TradingError("AGENT_TRADING_DISABLED")
    values = request if isinstance(request, dict) else request.model_dump()
    if values.get("version") != row.version:
        raise TradingError("INTENT_VERSION_CONFLICT")
    if values.get("previewHash") != row.preview_hash:
        raise TradingError("PREVIEW_CHANGED")
    if row.expires_ts <= time.time():
        raise TradingError("PREVIEW_EXPIRED")
    async with repository.session_factory() as session:
        states = await get_tool_states(session)
    tool = "prepare_cancel_order" if row.action == "cancel" else "prepare_trade"
    if not states.get(tool, True):
        raise TradingError("TOOL_DISABLED")
    return await validate_call(_call_for(row), repository.session_factory)


async def _admit(repository, row, request):
    async with get_admission_lock():
        row = await repository.get_intent(row.id, row.conversation_id)
        if row.status != "prepared":
            return row
        await _validate_first(repository, row, request)
        await admission.assert_auto_idle()
        admission.check_chain(row, await repository.list_unsettled())
        changes = {"cl_ord_id": None if row.action == "cancel"
                   else "agt" + uuid4().hex[:29]}
        return await repository.transition(row.id, row.version, "submitting", changes)


async def _execute(repository, row, request):
    trace = SubmissionTrace()
    admitted = False
    try:
        row = await _admit(repository, row, request)
        if row.status != "submitting":
            return row
        admitted = True
        _admitted[row.id].set()
        result = await asyncio.wait_for(_dispatch(repository, row, trace), timeout=10)
        return await repository.transition(row.id, row.version, "accepted", {
            "result_json": json.dumps(result), "next_check_ts": int(time.time())})
    except (TradingError, OKXPrivateError) as exc:
        if not admitted:
            raise
        status = "rejected" if trace.phase == "not_sent" or trace.ack == "rejected" else "unknown"
        result = {"ordId": trace.ord_id, "protectionStatus": "unverified"}
        if hasattr(exc, "target_result"):
            result.update(exc.target_result)
        return await repository.transition(row.id, row.version, status, {
            "error_code": exc.code, "result_json": json.dumps(result),
            "next_check_ts": int(time.time()) + 5})
    except Exception:
        if not admitted:
            raise
        await _preserve_unknown(repository, row.id)
        return await repository.get_intent(row.id)
    finally:
        if admitted:
            await _preserve_unknown(repository, row.id)


async def _preserve_unknown(repository, identity):
    try:
        current = await repository.get_intent(identity)
        if current and current.status == "submitting":
            await repository.transition(identity, current.version, "unknown", {
                "next_check_ts": int(time.time()) + 5})
    except Exception:
        # 持久 submitting 仍占用准入；核对器将在 owner 释放后继续恢复。
        admission.set_ready(False)
        logger.error("聊天提交结果无法持久化，已封闭恢复屏障")


async def _dispatch(repository, row, trace):
    from .binding import validate_call
    identity = await validate_call(_call_for(row), repository.session_factory)
    preview = json.loads(row.preview_json)
    preview["action"] = row.action
    if row.action != "cancel":
        preview["payload"]["clOrdId"] = row.cl_ord_id
    guard = DispatchGuard(preview, row.expires_ts, _enabled)
    guard.account_identity = identity
    guard.identity_checker = lambda: validate_call(_call_for(row), repository.session_factory)
    settings = get_settings().model_copy(deep=True)
    client = OKXTradeClient(identity.credentials.api_key,
                            identity.credentials.secret_key,
                            identity.credentials.passphrase, settings=settings)
    try:
        if row.action == "cancel":
            return await _cancel(client, row, guard, trace)
        await _recheck_account(client, row.action, preview, settings)
        guard.preview.update(preview)
        await validate_call(_call_for(row), repository.session_factory)
    finally:
        await client.aclose()
    async with repository.session_factory() as session:
        if row.action == "open":
            return await _open(session, identity, row, (guard, trace, settings))
        from ..okx.intent_close import IntentExecutionContext, place_intent_close
        body = preview["payload"]
        close = ClosePositionInput(
            body["instId"], body.get("posSide", "net"),
            float(preview["positionObservedSz"]) * preview["positionIdentity"]["netSign"],
            body["tdMode"], body["ordType"],
            float(body["px"]) if "px" in body else None, float(body["sz"]))
        context = IntentExecutionContext(row.account_id, row.cl_ord_id,
                                         settings, guard, trace)
        return await place_intent_close(session, identity.credentials, close, context)


async def _recheck_account(client, action, preview, settings):
    config = await client.get_account_config()
    if str(config.get("acctLv")) not in {"2", "3"}:
        raise TradingError("ACCOUNT_MODE_UNSUPPORTED")
    if (str(config.get("posMode")) != str(preview.get("posMode"))
            or str(config.get("acctLv")) != str(preview.get("acctLv"))):
        raise TradingError("PREVIEW_STALE")
    if action != "open":
        return
    body = preview["payload"]
    rows = await client.get_leverage_info(body["instId"], body["tdMode"])
    matches = [r for r in rows if r.get("instId") == body["instId"]
               and r.get("mgnMode") == body["tdMode"]
               and ((r.get("posSide") or "net") == body.get("posSide", "net")
                    or (body["tdMode"] == "cross" and not r.get("posSide")))]
    if len(matches) != 1 or _amount(matches[0].get("lever")) != _amount(preview.get("leverageObserved")):
        raise TradingError("PREVIEW_STALE")
    sizes = await client.get_max_size(body["instId"], body["tdMode"], body.get("px"))
    balance = await client.get_balance()
    _check_capacity(preview, sizes, balance)


def _check_capacity(preview, sizes, balance):
    body = preview["payload"]
    sizes = [row for row in sizes if row.get("instId") == body["instId"]]
    if len(sizes) != 1:
        raise TradingError("ORDER_CAPACITY_UNAVAILABLE")
    maximum = sizes[0].get("maxBuy" if body["side"] == "buy" else "maxSell")
    rows = [r for r in balance.get("details", []) if r.get("ccy") == "USDT"]
    if maximum is None or len(rows) != 1 or rows[0].get("availEq") in (None, ""):
        raise TradingError("ACCOUNT_SNAPSHOT_INCOMPLETE")
    if Decimal(body["sz"]) > _amount(maximum):
        raise TradingError("INSUFFICIENT_BALANCE")
    preview["availableMarginUsdt"] = str(_amount(rows[0]["availEq"]))


def _amount(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise TradingError("ACCOUNT_SNAPSHOT_INCOMPLETE") from None
    if not number.is_finite() or number < 0:
        raise TradingError("ACCOUNT_SNAPSHOT_INCOMPLETE")
    return number


async def _open(session, identity, row, context):
    from ..okx.trading import place_live_order
    guard, trace, settings = context
    body = guard.preview["payload"]
    attach = (body.get("attachAlgoOrds") or [{}])[0]
    order = IntentOrderInput(
        body["instId"], "long" if body["side"] == "buy" else "short",
        body["ordType"], body["tdMode"],
        float(body["px"]) if "px" in body else None,
        float(body["sz"]), None,
        float(attach["tpTriggerPx"]) if attach.get("tpTriggerPx") else None,
        float(attach["slTriggerPx"]) if attach.get("slTriggerPx") else None,
        row.cl_ord_id, trace, guard)
    try:
        result = await place_live_order(session, identity.credentials,
                                       row.account_id, order, settings=settings)
    finally:
        if trace.phase == "sent" and trace.ack != "rejected":
            _invalidate_account_cache(identity, settings)
    result["protectionStatus"] = "unverified"
    return result


def _invalidate_account_cache(identity, settings):
    """不确定写的保守失效由聊天入口承担，不改变旧服务失败语义。"""
    from ..okx.client_pool import fingerprint
    from ..okx.positions_cache import get_positions_cache
    try:
        key = fingerprint(identity.credentials.api_key, settings.okx_rest_base,
                          settings.okx_account_demo, settings.okx_account_timeout_seconds)
        get_positions_cache().invalidate(key)
    except Exception:
        logger.debug("聊天交易持仓缓存失效未完成")


async def _cancel(client, row, guard, trace):
    from ..okx.trading import _drop_positions_cache
    body = guard.preview["payload"]
    target = await client.get_order(body["instId"], ord_id=row.target_ord_id)
    if (not target or str(target.get("ordId")) != row.target_ord_id
            or target.get("instId") != body["instId"]):
        raise TradingError("ORDER_NOT_FOUND")
    if target.get("state") in {"filled", "canceled", "mmp_canceled"}:
        exc = TradingError("PREVIEW_STALE", "目标订单已结束，无需撤销")
        exc.target_result = {"exchangeState": target["state"],
                             "ordId": row.target_ord_id}
        raise exc
    snapshot = guard.preview.get("targetSnapshot", {})
    if (target.get("state") not in {"live", "partially_filled"}
            or any(str(target.get(key) or "") != str(value or "")
                   for key, value in snapshot.items()
                   if key not in {"state", "accFillSz", "uTime"})):
        raise TradingError("PREVIEW_STALE")
    evidence = DispatchEvidence(body, "", None, time.monotonic())
    def before_send():
        guard.validate(evidence)
        trace.phase = "sent"
    before_send.prepare = guard.refresh_identity
    try:
        result = await client.cancel_order(
            body["instId"], ord_id=row.target_ord_id,
            options=SendOptions(False, before_send))
        if str(result.get("ordId") or "") != row.target_ord_id:
            raise OKXPrivateError("OKX_EMPTY_ORDER_ACK", "撤销回执身份尚未确认")
        trace.ack, trace.ord_id = "accepted", row.target_ord_id
        return {"ordId": row.target_ord_id, "exchangeState": target.get("state")}
    except OKXPrivateError as exc:
        if exc.business_rejected:
            trace.ack = "rejected"
        raise
    finally:
        if trace.phase == "sent" and trace.ack != "rejected":
            _drop_positions_cache(client)
