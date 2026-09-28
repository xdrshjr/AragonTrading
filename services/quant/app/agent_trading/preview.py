"""确定性 Decimal 预览：冻结事实并核对旧 builder 实际序列化值。"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_EVEN

from ..config import get_settings
from ..okx.order_builder import (
    ClosePositionInput,
    InstrumentMeta,
    OrderInput,
    build_close_order_payload,
    build_order_payload,
)
from ..okx.rest_client import get_rest_client, infer_inst_type
from ..okx.trade_client import OKXTradeClient
from .errors import TradingError
from .read_service import fetch_open_orders, position_key, project_balance


def number(value, code="INVALID_SIZE"):
    """严格正数解析；原始交易所空值不可补零。"""
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise TradingError(code) from exc
    if not result.is_finite() or result <= 0 or abs(result.adjusted()) > 18:
        raise TradingError(code)
    return result


def decimal_string(value):
    """输出标准十进制，不使用指数表示。"""
    return format(value.normalize(), "f")


def checked_float(value):
    """跨旧 float 服务边界前要求十进制可无损往返。"""
    if value is None:
        return None
    converted = float(value)
    if Decimal(str(converted)) != value:
        raise TradingError("PRECISION_UNSUPPORTED")
    return converted


def _meta(raw):
    if raw.get("ctType") != "linear" or raw.get("settleCcy") != "USDT":
        raise TradingError("UNSUPPORTED_ACTION")
    if not raw.get("ctValCcy") or raw["ctValCcy"] in {"USD", "USDT", "USDC"}:
        raise TradingError("UNSUPPORTED_ACTION")
    fields = {
        key: number(raw.get(key), "PRECISION_UNSUPPORTED")
        for key in ("ctVal", "lotSz", "minSz", "tickSz")
    }
    values = InstrumentMeta(
        ct_val=checked_float(fields["ctVal"]),
        ct_val_ccy=raw.get("ctValCcy", ""),
        lot_sz=checked_float(fields["lotSz"]),
        min_sz=checked_float(fields["minSz"]),
        tick_sz=checked_float(fields["tickSz"]),
        max_lever=1,
        ct_type="linear",
        settle_ccy="USDT",
    )
    return fields, values


def _snap_price(value, step):
    if value is None:
        return None
    result = (number(value) / step).to_integral_value(
        rounding=ROUND_HALF_EVEN
    ) * step
    return number(result)


def _find_position(request, facts):
    matches = [
        row
        for row in facts["positions"]
        if position_key(row) == request.positionKey
    ]
    if len(matches) != 1:
        raise TradingError("POSITION_CHANGED")
    row = matches[0]
    if not row.get("posId") or not str(row.get("cTime", "")).isdigit():
        raise TradingError("POSITION_IDENTITY_UNAVAILABLE")
    if int(row["cTime"]) <= 0:
        raise TradingError("POSITION_IDENTITY_UNAVAILABLE")
    return row


def _base_preview(request, facts, constraints):
    config = facts["config"]
    if str(config.get("acctLv")) not in {"2", "3"}:
        raise TradingError("ACCOUNT_MODE_UNSUPPORTED")
    if config.get("posMode") not in {"net_mode", "long_short_mode"}:
        raise TradingError("ACCOUNT_MODE_UNSUPPORTED")
    price = _snap_price(request.px, constraints["tickSz"])
    reference = price or number(facts.get("ticker"), "ACCOUNT_READ_FAILED")
    empty = {
        key: None
        for key in (
            "direction",
            "posSide",
            "tdMode",
            "sizeContracts",
            "notionalEstimateUsdt",
            "marginEstimateUsdt",
            "leverageObserved",
            "tpTriggerPx",
            "slTriggerPx",
            "positionObservedSz",
            "positionIdentity",
            "maxOpenContracts",
        )
    }
    return {
        **empty,
        "schemaVersion": 1,
        "ordType": request.ordType,
        "price": decimal_string(price) if price else None,
        "referencePrice": decimal_string(reference),
        "warnings": [],
        "observedTs": int(time.time()),
        "accountMode": "live",
        "acctLv": str(config["acctLv"]),
        "posMode": config["posMode"],
        "instrumentConstraints": {
            **{
                key: decimal_string(value) for key, value in constraints.items()
            },
            "ctValCcy": facts["meta"].get("ctValCcy"),
        },
        "protectionStatus": "unverified",
    }


def _size(value, constraints):
    result = (value / constraints["lotSz"]).to_integral_value(
        rounding=ROUND_FLOOR
    ) * constraints["lotSz"]
    if result < constraints["minSz"] or result <= 0:
        raise TradingError("INVALID_SIZE")
    return result


def _open_capacity(request, facts, preview, size):
    matches = [
        row
        for row in facts.get("leverage", [])
        if row.get("instId") == request.instId
        and row.get("mgnMode") == request.tdMode
        and (
            (row.get("posSide") or "net") == (preview["posSide"] or "net")
            or (request.tdMode == "cross" and not row.get("posSide"))
        )
    ]
    if len(matches) != 1:
        raise TradingError("LEVERAGE_UNAVAILABLE")
    leverage = number(matches[0].get("lever"), "LEVERAGE_UNAVAILABLE")
    capacity = [
        row
        for row in facts.get("capacity", [])
        if row.get("instId") == request.instId
    ]
    if len(capacity) != 1:
        raise TradingError("ORDER_CAPACITY_UNAVAILABLE")
    maximum = number(
        capacity[0].get("maxBuy" if request.direction == "long" else "maxSell"),
        "ORDER_CAPACITY_UNAVAILABLE",
    )
    notional = (
        size
        * number(preview["referencePrice"])
        * number(facts["meta"]["ctVal"])
    )
    margin = notional / leverage
    available = number(
        project_balance(facts["balance"], facts["config"])[
            "availableMarginUsdt"
        ],
        "INSUFFICIENT_BALANCE",
    )
    if size > maximum or margin > available:
        raise TradingError("INSUFFICIENT_BALANCE")
    buffer = Decimal("1.005") if request.ordType == "market" else Decimal(1)
    if notional * buffer > Decimal(
        str(get_settings().live_max_order_notional_usd)
    ):
        raise TradingError("INVALID_SIZE", "预估名义金额超过当前单笔上限")
    preview.update(
        notionalEstimateUsdt=decimal_string(notional),
        marginEstimateUsdt=decimal_string(margin),
        leverageObserved=decimal_string(leverage),
        maxOpenContracts=decimal_string(maximum),
    )


def _build_open(request, facts, state):
    preview, constraints, meta = state
    if not facts["orders"]["complete"]:
        raise TradingError("ACCOUNT_SNAPSHOT_INCOMPLETE")
    reference = number(preview["referencePrice"])
    requested = number(request.size.value)
    if request.size.unit == "notional_usdt":
        requested /= constraints["ctVal"] * reference
    size = _size(requested, constraints)
    preview.update(
        instId=request.instId,
        direction=request.direction,
        tdMode=request.tdMode,
        posSide=request.direction
        if preview["posMode"] == "long_short_mode"
        else "net",
    )
    _check_net_position(request, facts)
    _open_capacity(request, facts, preview, size)
    triggers = [
        _snap_price(value, constraints["tickSz"])
        for value in (request.tpTriggerPx, request.slTriggerPx)
    ]
    _validate_triggers(triggers, reference, request.direction)
    order = OrderInput(
        request.instId,
        request.direction,
        request.ordType,
        request.tdMode,
        checked_float(number(preview["price"])) if preview["price"] else None,
        checked_float(size),
        None,
        checked_float(triggers[0]),
        checked_float(triggers[1]),
    )
    preview["tpTriggerPx"], preview["slTriggerPx"] = [
        decimal_string(v) if v else None for v in triggers
    ]
    preview["warnings"] = (
        ["已请求随单止盈/止损，保护尚未核实"]
        if any(triggers)
        else ["未设置止损"]
    )
    return size, build_order_payload(order, meta, preview["posMode"], "")


def _validate_triggers(triggers, reference, direction):
    for index, trigger in enumerate(triggers):
        if trigger and (trigger > reference) != (
            (direction == "long") == (index == 0)
        ):
            raise TradingError(
                "AMBIGUOUS_TRADE", "止盈止损价格与持仓方向不匹配"
            )
        if trigger == reference:
            raise TradingError("AMBIGUOUS_TRADE", "止盈止损价格不能等于参考价")


def _check_net_position(request, facts):
    if facts["config"].get("posMode") != "net_mode":
        return
    for position in facts["positions"]:
        if position.get("instId") != request.instId:
            continue
        try:
            value = Decimal(position["pos"])
        except (InvalidOperation, KeyError, TypeError) as exc:
            raise TradingError("ACCOUNT_READ_FAILED") from exc
        if not value.is_finite():
            raise TradingError("ACCOUNT_READ_FAILED")
        if value and (value > 0) != (request.direction == "long"):
            raise TradingError(
                "AMBIGUOUS_TRADE", "当前净仓方向相反，请明确使用平仓指令"
            )


def _build_close(request, facts, state):
    preview, constraints, meta = state
    position = _find_position(request, facts)
    signed = Decimal(position["pos"])
    observed = number(abs(signed), "POSITION_CHANGED")
    size = _size(
        observed
        if request.closeSize == "all"
        else number(request.closeSize.contracts),
        constraints,
    )
    if size > observed:
        raise TradingError("POSITION_CHANGED")
    pos_side = position.get("posSide") or "net"
    direction = (
        ("long" if signed > 0 else "short") if pos_side == "net" else pos_side
    )
    if pos_side not in {"net", "long", "short"} or position.get(
        "mgnMode"
    ) not in {"cross", "isolated"}:
        raise TradingError("POSITION_CHANGED")
    preview.update(
        instId=position["instId"],
        direction=direction,
        posSide=pos_side,
        tdMode=position["mgnMode"],
        positionObservedSz=decimal_string(observed),
        positionIdentity=_position_identity(position, signed),
    )
    close = ClosePositionInput(
        position["instId"],
        pos_side,
        checked_float(signed),
        position["mgnMode"],
        request.ordType,
        checked_float(number(preview["price"])) if preview["price"] else None,
        checked_float(size),
    )
    payload = build_close_order_payload(
        close,
        meta,
        preview["posMode"],
        checked_float(size),
        "",
        order_type=request.ordType,
    )
    preview["warnings"] = ["仅平掉本次预览冻结数量；外部交易可能改变仓位"]
    return size, payload


def _position_identity(position, signed):
    return {
        "posId": position["posId"],
        "exchangeCTimeMs": str(position["cTime"]),
        "netSign": 1 if signed > 0 else -1,
        "mgnMode": position["mgnMode"],
    }


def build_preview(request, facts):
    """使用已取得的原始证据生成不可变订单预览，不执行任何交易。"""
    constraints, meta = _meta(facts["meta"])
    preview = _base_preview(request, facts, constraints)
    builder = _build_open if request.action == "open" else _build_close
    size, payload = builder(request, facts, (preview, constraints, meta))
    payload.pop("clOrdId", None)
    if Decimal(payload["sz"]) != size:
        raise TradingError("PRECISION_UNSUPPORTED")
    if preview["price"] and Decimal(payload["px"]) != Decimal(preview["price"]):
        raise TradingError("PRECISION_UNSUPPORTED")
    attached = (payload.get("attachAlgoOrds") or [{}])[0]
    for key in ("tpTriggerPx", "slTriggerPx"):
        if preview[key] and Decimal(attached.get(key, "0")) != Decimal(
            preview[key]
        ):
            raise TradingError("PRECISION_UNSUPPORTED")
    preview.update(
        payload=payload,
        side=payload["side"],
        reduceOnly=payload.get("reduceOnly", False),
        sizeContracts=decimal_string(Decimal(payload["sz"])),
    )
    return preview


async def load_facts(identity, request):
    """新鲜只读取数，公共价格和规则保留原始十进制。"""
    credentials = identity.credentials
    client = OKXTradeClient(
        credentials.api_key, credentials.secret_key, credentials.passphrase
    )
    try:
        async with asyncio.timeout(15):
            return await _load_from_clients(client, get_rest_client(), request)
    except TradingError:
        raise
    except Exception as exc:
        raise TradingError("ACCOUNT_READ_FAILED") from exc
    finally:
        await client.aclose()


async def _load_from_clients(client, rest, request):
    facts = {
        "config": await client.get_account_config(),
        "positions": await client.get_positions(),
    }
    inst_id = (
        request.instId
        if request.action == "open"
        else _find_position(request, facts)["instId"]
    )
    if infer_inst_type(inst_id) not in {"SWAP", "FUTURES"}:
        raise TradingError("UNSUPPORTED_ACTION")
    rows = await rest._request(
        "/api/v5/public/instruments",
        {"instType": infer_inst_type(inst_id), "instId": inst_id},
    )
    facts["meta"] = next(
        (row for row in rows if row.get("instId") == inst_id), {}
    )
    ticker = await rest._request("/api/v5/market/ticker", {"instId": inst_id})
    facts["ticker"] = ticker[0].get("last") if ticker else None
    if request.action == "open":
        facts["balance"] = await client.get_balance()
        facts["orders"] = await fetch_open_orders(client)
        facts["leverage"] = await client.get_leverage_info(
            inst_id, request.tdMode
        )
        normalized_px = _snap_price(
            request.px, number(facts["meta"].get("tickSz"))
        )
        facts["capacity"] = await client.get_max_size(
            inst_id,
            request.tdMode,
            decimal_string(normalized_px) if normalized_px else None,
        )
    return facts
