"""实时账户读取白名单，禁止将失败表示为空仓或把部分覆盖称为全部。"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from ..db.models import OkxAccount
from ..db.session import get_sessionmaker
from ..okx.trade_client import OKXTradeClient
from .binding import current_environment, validate_call
from .errors import TradingError
from .schemas import (
    ContextQuery,
    EmptyRequest,
    FillQuery,
    OrderStatusQuery,
    PositionQuery,
    parse_request,
)

READ_TIMEOUT_S = 15
MODEL_RESULT_MAX_BYTES = 24 * 1024
_QUERY_TYPES = {
    "accounts": EmptyRequest,
    "balance": EmptyRequest,
    "positions": PositionQuery,
    "orders": PositionQuery,
    "fills": FillQuery,
    "order": OrderStatusQuery,
    "context": ContextQuery,
}


def decimal_or_none(value):
    """空值和非法值保留未知，不补零也不经过 float。"""
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    if not number.is_finite() or abs(number.adjusted()) > 30:
        return None
    return format(number.normalize(), "f")


def position_key(row):
    """非授权性质的仓位定位键，每次仍须在绑定账户匹配。"""
    identity = [
        row.get("instId"),
        row.get("posSide") or "net",
        row.get("mgnMode"),
    ]
    return (
        base64.urlsafe_b64encode(
            json.dumps(identity, separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )


def _field_units(row):
    """单位随字段和产品变化，混合账户不能整体假定为张或 USDT。"""
    parts = str(row.get("instId") or "").split("-")
    base, quote = (parts[:2] if len(parts) >= 2 else (None, None))
    kind = row.get("instType")
    if not kind:
        kind = "SPOT" if len(parts) == 2 else "CONTRACT"
    quantity = base if kind in {"SPOT", "MARGIN"} else "contracts"
    price = row.get("ccy") if kind == "OPTION" else quote
    units = {
        **dict.fromkeys(("sz", "fillSz", "accFillSz"), quantity),
        **dict.fromkeys(
            ("px", "avgPx", "fillPx", "markPx", "last",
             "tpTriggerPx", "slTriggerPx"), price
        ),
        **dict.fromkeys(("margin", "upl", "fillPnl"), row.get("ccy")),
        "pos": row.get("posCcy") if kind == "MARGIN" else quantity,
        "fee": row.get("feeCcy"),
    }
    if kind == "SPOT" and row.get("ordType") == "market":
        target = row.get("tgtCcy") or (
            "quote_ccy" if row.get("side") == "buy" else "base_ccy"
        )
        units["sz"] = quote if target == "quote_ccy" else base
    return units


def project_position(row):
    """交易所原始持仓只投影允许字段，所有金融数值保持十进制串。"""
    fields = (
        "pos",
        "avgPx",
        "last",
        "markPx",
        "upl",
        "uplRatio",
        "lever",
        "margin",
    )
    result = {key: decimal_or_none(row.get(key)) for key in fields}
    result.update(
        {
            key: row.get(key) or None
            for key in ("instId", "instType", "posSide", "mgnMode", "posId")
        }
    )
    result.update(
        fieldUnits=_field_units(row),
        positionKey=position_key(row),
        exchangeCTimeMs=row.get("cTime") or None,
        exchangeUTimeMs=row.get("uTime") or None,
    )
    return result


def project_balance(balance, config):
    """USDT availEq 是适用模式的保证金证据，availBal 保持独立。"""
    details = [
        {
            "ccy": row.get("ccy"),
            "fieldUnits": {
                **dict.fromkeys(
                    ("eq", "availBal", "availEq", "frozenBal"),
                    row.get("ccy"),
                ),
                "eqUsd": "USD",
            },
            **{
                key: decimal_or_none(row.get(key))
                for key in ("eq", "availBal", "availEq", "frozenBal", "eqUsd")
            },
        }
        for row in balance.get("details", [])
    ]
    usdt = next((row for row in details if row["ccy"] == "USDT"), {})
    return {
        "totalEqUsd": decimal_or_none(balance.get("totalEq")),
        "availableBalanceUsdt": usdt.get("availBal"),
        "availableMarginUsdt": usdt.get("availEq")
        if str(config.get("acctLv")) in {"2", "3"}
        else None,
        "details": details,
    }


def project_order(row, kind="normal"):
    """普通单与算法单身份分开，算法只能读取。"""
    result = {
        key: row.get(key) or None
        for key in (
            "instId",
            "ordId",
            "algoId",
            "clOrdId",
            "side",
            "posSide",
            "state",
            "ordType",
            "tdMode",
            "cTime",
            "uTime",
        )
    }
    result.update(
        {
            key: decimal_or_none(row.get(key))
            for key in (
                "sz",
                "px",
                "accFillSz",
                "avgPx",
                "tpTriggerPx",
                "slTriggerPx",
            )
        }
    )
    result.update(
        kind=kind, rowKey=f"{kind}:{row.get('ordId') or row.get('algoId')}",
        fieldUnits=_field_units(row),
    )
    return result


def project_fill(row):
    """成交事实不包含 uid 或原始响应。"""
    result = {
        key: row.get(key) or None
        for key in (
            "instId",
            "tradeId",
            "ordId",
            "side",
            "posSide",
            "feeCcy",
            "ts",
        )
    }
    result.update(
        {
            key: decimal_or_none(row.get(key))
            for key in ("fillPx", "fillSz", "fee", "fillPnl")
        }
    )
    result["fieldUnits"] = _field_units(row)
    return result


async def list_accounts(session_factory=None):
    """账户目录白名单，无 keyPreview、权限原文或凭据。"""
    async with (session_factory or get_sessionmaker())() as session:
        rows = (
            await session.scalars(select(OkxAccount).order_by(OkxAccount.id))
        ).all()
    return [
        {
            "id": row.id,
            "label": row.label,
            "isDefault": row.is_default,
            "configured": bool(
                row.api_key and row.secret_key and row.passphrase
            ),
        }
        for row in rows
    ]


async def _fetch_pages(client, query, deadline):
    path, kind, inst_id = query
    rows, warnings, cursor, successful = [], [], None, False
    for _ in range(5):
        params = {"limit": "100"}
        if inst_id:
            params["instId"] = inst_id
        if kind != "normal":
            params["ordType"] = kind
        if cursor:
            params["after"] = cursor
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            page = await asyncio.wait_for(client._get(path, params), remaining)
            identity_key = "ordId" if kind == "normal" else "algoId"
            if any(
                not row.get(identity_key) or not row.get("instId")
                for row in page
            ):
                raise TradingError("ACCOUNT_SNAPSHOT_INCOMPLETE")
        except Exception:
            warnings.append(f"{kind} 委托读取未完成")
            return rows, False, warnings, successful
        successful = True
        rows.extend(
            projected for row in page for projected in _pending_rows(row, kind)
        )
        if len(page) < 100:
            return rows, True, warnings, successful
        next_cursor = str(page[-1][identity_key])
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
    return rows, False, [f"{kind} 委托达到分页上限"], successful


def _pending_rows(row, kind):
    projected = project_order(row, kind)
    if kind != "oco":
        return [projected]
    return [
        dict(projected, rowKey=f"{projected['rowKey']}:{leg}", leg=leg)
        for leg in ("tp", "sl")
        if row.get(f"{leg}TriggerPx")
    ]


async def fetch_open_orders(client, inst_id=None):
    """三个端点分别有界分页，端点失败保留已读数据及覆盖警告。"""
    deadline = time.monotonic() + READ_TIMEOUT_S
    complete, rows, warnings, successful = True, [], [], False
    for kind in ("normal", "oco", "conditional"):
        path = (
            "/api/v5/trade/orders-pending"
            if kind == "normal"
            else "/api/v5/trade/orders-algo-pending"
        )
        page, done, notes, observed = await _fetch_pages(
            client, (path, kind, inst_id), deadline
        )
        rows.extend(page)
        complete = complete and done
        successful = successful or observed
        warnings.extend(notes)
    if not successful:
        raise TradingError("ACCOUNT_READ_FAILED")
    unique = {row["rowKey"]: row for row in rows}
    return {
        "rows": list(unique.values()),
        "complete": complete,
        "warnings": warnings,
    }


def bound_snapshot(snapshot):
    """缩减条数而非截断 JSON，24 KiB 上限包含全部元数据。"""
    while (
        len(json.dumps(snapshot, ensure_ascii=False).encode())
        > MODEL_RESULT_MAX_BYTES
    ):
        rows = snapshot.get("data")
        if isinstance(rows, dict):
            rows = rows.get("details", rows.get("groups"))
        if not isinstance(rows, list) or not rows:
            raise TradingError("ACCOUNT_READ_FAILED")
        rows.pop()
        snapshot.update(truncated=True, returnedCount=len(rows), hasMore=True)
    return snapshot


def _envelope(identity, started, result, params):
    contents, complete, warnings = result
    observed = len(contents) if isinstance(contents, list) else None
    if isinstance(contents, list):
        contents.sort(
            key=lambda row: str(
                row.get("positionKey")
                or row.get("rowKey")
                or row.get("tradeId")
                or row.get("id")
            )
        )
        offset = getattr(params, "offset", 0)
        limit = getattr(params, "limit", len(contents))
        contents = contents[offset : offset + limit]
    returned = len(contents) if isinstance(contents, list) else None
    return bound_snapshot(
        {
            "schemaVersion": 1,
            "source": "okx",
            "accountId": identity.account_id if identity else None,
            "accountLabel": identity.label if identity else None,
            "environment": identity.environment
            if identity
            else current_environment(),
            "observedStartTs": started,
            "asOfTs": int(time.time()),
            "coverage": {"status": "complete" if complete else "partial"},
            "warnings": warnings,
            "data": contents,
            "total": observed if complete else None,
            "observedCount": observed,
            "returnedCount": returned,
            "truncated": False,
            "hasMore": not complete
            or (
                observed is not None
                and observed > getattr(params, "offset", 0) + returned
            ),
        }
    )


async def _read_from_client(client, kind, params):
    if kind == "balance":
        balance, config = await asyncio.gather(
            client.get_balance(), client.get_account_config()
        )
        return project_balance(balance, config), True, []
    if kind == "positions":
        rows = await client.get_positions()
        rows = [
            project_position(row)
            for row in rows
            if not params.instId or row.get("instId") == params.instId
        ]
        return rows, True, []
    if kind == "orders":
        orders = await fetch_open_orders(client, params.instId)
        return orders["rows"], orders["complete"], orders["warnings"]
    if kind == "fills":
        rows = await client.get_fills(params.instId, params.limit)
        return (
            [project_fill(row) for row in rows],
            False,
            ["仅为近期可查成交范围"],
        )
    rows = await client.get_order(params.instId, ord_id=params.ordId)
    row = rows[0] if isinstance(rows, list) and rows else rows
    if not row:
        raise TradingError("ORDER_NOT_FOUND")
    if row.get("instId") != params.instId or row.get("ordId") != params.ordId:
        raise TradingError("IDENTITY_MISMATCH")
    return project_order(row), True, []


async def read_account(kind, params, call, session_factory=None):
    """工具与 REST 共用读取服务，所有网络操作共同服从 15 秒期限。"""
    request = parse_request(_QUERY_TYPES[kind], params)
    started = int(time.time())
    if kind == "accounts":
        return _envelope(
            None,
            started,
            (await list_accounts(session_factory), True, []),
            request,
        )
    if kind == "order" and request.intentId:
        return await _read_intent(request.intentId, call, session_factory)
    identity = await validate_call(call, session_factory)
    if kind == "context":
        from .auto_context import get_context

        result = await get_context(identity, request.instId)
        await validate_call(call, session_factory)
        snapshot = _envelope(identity, started, (result, True, []), request)
        snapshot["source"] = "local_auto_trade"
        return bound_snapshot(snapshot)
    credentials = identity.credentials
    client = OKXTradeClient(
        credentials.api_key, credentials.secret_key, credentials.passphrase
    )
    try:
        result = await asyncio.wait_for(
            _read_from_client(client, kind, request),
            # 分页器自行控制总期限，以便超时仍返回已完成页的部分证据。
            None if kind == "orders" else READ_TIMEOUT_S,
        )
        await validate_call(call, session_factory)
        snapshot = _envelope(identity, started, result, request)
        if kind == "orders":
            snapshot["coverage"].update(
                includedKinds=["normal", "oco", "conditional"],
                excludedKinds=["trigger", "move_order_stop", "iceberg", "twap"],
            )
        return bound_snapshot(snapshot)
    except TradingError:
        raise
    except Exception as exc:
        raise TradingError("ACCOUNT_READ_FAILED") from exc
    finally:
        await client.aclose()


async def _read_intent(intent_id, call, session_factory):
    from ..db.agent_trade_repositories import AgentTradeRepository
    from .result_view import intent_view

    if call is None:
        raise TradingError("INTENT_NOT_FOUND")
    row = await AgentTradeRepository(
        session_factory or get_sessionmaker()
    ).get_intent(intent_id, call.conversation_id)
    if row is None:
        raise TradingError("INTENT_NOT_FOUND")
    return intent_view(row)
