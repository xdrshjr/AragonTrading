"""聊天平仓只发送冻结张数，不调用整仓平仓或自动撤销端点。"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from decimal import Decimal

from ..agent_trading.dispatch_guard import DispatchEvidence
from ..db import repositories as repo
from .order_builder import (ClosePositionInput, SubmissionTrace,
                            build_close_order_payload, validate_close)
from .private_client import OKXPrivateError
from .trade_client import OKXTradeClient


@dataclass(frozen=True)
class IntentExecutionContext:
    """冻结身份、发送守卫和已持久化订单标识；不含外部输入。"""
    account_id: int
    client_order_id: str
    settings: object
    dispatch_guard: object
    trace: SubmissionTrace


def match_position(positions, preview):
    """仓位身份不变且未减少，才允许按原冻结张数平仓。"""
    identity = preview["positionIdentity"]
    payload = preview["payload"]
    for row in positions:
        if (row.get("instId") != payload["instId"]
                or row.get("mgnMode") != identity["mgnMode"]):
            continue
        if str(row.get("posId") or "") != identity["posId"]:
            continue
        if (row.get("posSide") or "net") != (payload.get("posSide") or "net"):
            continue
        pos = Decimal(str(row.get("pos") or 0))
        actual = {"posId": str(row.get("posId") or ""),
                  "exchangeCTimeMs": str(row.get("cTime") or ""),
                  "netSign": 1 if pos > 0 else -1,
                  "mgnMode": row.get("mgnMode")}
        if (actual == identity and abs(pos) >= Decimal(
                preview["positionObservedSz"])
                and abs(pos) >= Decimal(payload["sz"])):
            return row
    raise OKXPrivateError("POSITION_CHANGED", "持仓身份或数量已变化，请重新预览")


async def place_intent_close(session, creds, close: ClosePositionInput,
                             context: IntentExecutionContext) -> dict:
    """只发送一次指定数量普通订单，并复用手工交易审计格式。"""
    from . import trading
    client = OKXTradeClient(creds.api_key, creds.secret_key,
                            creds.passphrase, settings=context.settings)
    try:
        observed = time.monotonic()
        config = await client.get_account_config()
        positions = await client.get_positions()
        row = match_position(positions, context.dispatch_guard.preview)
        current = replace(close, pos=float(row["pos"]),
                          pos_side=str(row.get("posSide") or "net"))
        meta = await trading._load_meta(close.inst_id, fresh=True)
        trading.ensure_instrument_supported(meta)
        quantity = validate_close(current, meta, abs(current.pos),
                                  order_type=current.ord_type)["sz"]
        payload = build_close_order_payload(
            current, meta, str(config.get("posMode") or ""),
            quantity, context.client_order_id, order_type=current.ord_type)
        ticker = (await trading.get_rest_client().get_ticker(close.inst_id)
                  if current.ord_type == "market" else current.px)
        evidence = DispatchEvidence(payload, str(config.get("posMode") or ""),
                                    ticker, observed, meta, positions,
                                    str(config.get("acctLv") or ""))
        def before_send():
            context.dispatch_guard.validate(evidence)
            context.trace.phase = "sent"
        before_send.prepare = context.dispatch_guard.refresh_identity
        result = await client.place_order(
            payload, allow_retry=False, before_send=before_send)
        ord_id = str(result.get("ordId") or "")
        if not ord_id:
            raise OKXPrivateError("OKX_EMPTY_ORDER_ACK", "委托身份尚未确认")
        context.trace.ack, context.trace.ord_id = "accepted", ord_id
        await _audit_close(session, current, context, ord_id)
        return {"ordId": ord_id, "clOrdId": context.client_order_id}
    except OKXPrivateError as exc:
        if context.trace.phase == "sent" and exc.business_rejected:
            context.trace.ack = "rejected"
        raise
    finally:
        if context.trace.phase == "sent" and context.trace.ack != "rejected":
            trading._drop_positions_cache(client)
        await client.aclose()


async def _audit_close(session, close, context, ord_id):
    """写入已受理订单审计；失败由执行器保守保留未知态。"""
    values = repo.LiveOrderAuditValues(
        account_id=context.account_id, inst_id=close.inst_id,
        direction=("long" if close.pos > 0 else "short")
        if close.pos_side == "net" else close.pos_side,
        ord_type=close.ord_type, td_mode=close.mgn_mode,
        px=close.px, sz=close.sz, lever=None,
        tp_trigger_px=None, sl_trigger_px=None, status="accepted",
        cl_ord_id=context.client_order_id, ord_id=ord_id, error_code="",
        created_ts=int(time.time()))
    await repo.create_live_order_audit(session, values=values)
