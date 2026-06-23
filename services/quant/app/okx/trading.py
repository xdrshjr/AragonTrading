"""实盘交易服务层（spec §2.2/§2.3/§6）：编排「设杠杆→下单」、错误归一、脱敏、安全护栏。

职责：把库内凭证解析为 ``OKXTradeClient``，对外提供下单 / 设杠杆 / 查持仓 / 查成交 / 取下单上下文；
统一以 ``_map_trade_code`` 归一错误码、以 ``_redact`` 脱敏 message（C7 单点收口，复用 accounts 实现）。

安全护栏（一等需求）：
- 名义上限本地拦截（``order_builder.validate_order`` → ``LIVE_ORDER_REJECTED_LOCAL``）。
- net_mode 反向单本地拒（``LIVE_WOULD_REDUCE_POSITION``）：守任务硬约束「不能动现有持仓」（C0-1）。
- 仅 USDT 本位 SWAP/FUTURES（``LIVE_INSTRUMENT_UNSUPPORTED``，C1-4）。
- 唯一 clOrdId 防重（C1-2）；每单留痕 ``live_order_audit``（不存任何密钥）。
"""
from __future__ import annotations

import asyncio
import itertools
import time

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db import repositories as repo
from .accounts import AccountCredentials, _map_okx_code, _normalize_position, _redact
from .order_builder import (
    InstrumentMeta,
    OrderInput,
    build_order_payload,
    ensure_instrument_supported,
    is_instrument_supported,
    validate_order,
)
from .private_client import OKXPrivateError
from .rest_client import OKXError, get_rest_client, infer_inst_type
from .trade_client import OKXTradeClient
from .types import _optional_float

# 市价单名义上限校验的保守滑点缓冲（§2.4 / C2-3）：买多放大、买空缩小参考价以免越限漏拦。
_MARKET_NOTIONAL_BUFFER = 0.02
# 本地拦截类语义码（审计 status=rejected；非此集合的 OKX 业务失败 status=error）。
_LOCAL_REJECT_CODES = {
    "LIVE_ORDER_REJECTED_LOCAL", "LIVE_WOULD_REDUCE_POSITION", "LIVE_INSTRUMENT_UNSUPPORTED"}
# clOrdId 进程内单调序号（与毫秒时间戳拼接，确保同毫秒并发也唯一，C1-2）。
_clord_counter = itertools.count()


def _map_trade_code(code: str) -> str:
    """OKX 下单 / 本地拦截码 → 稳定对外语义码（在 accounts._map_okx_code 基础上补下单类，§6）。"""
    mapping = {
        "51008": "INSUFFICIENT_BALANCE",   # 可用余额 / 保证金不足
        "51004": "LEVERAGE_EXCEEDS",       # 数量超当前杠杆可开上限
        "51000": "ORDER_PARAM_INVALID",    # 下单参数不合法
        "51001": "ORDER_PARAM_INVALID",
        "51010": "ACCT_MODE_MISMATCH",     # 账户模式与下单方式不匹配
    }
    if code in mapping:
        return mapping[code]
    if code in _LOCAL_REJECT_CODES or code == "LIVE_TRADING_DISABLED":
        return code
    return _map_okx_code(code)


def _gen_cl_ord_id() -> str:
    """生成唯一 clOrdId（前缀 aragon + 毫秒 hex + 序号，alnum 且 ≤32 位，C1-2）。"""
    ms = int(time.time() * 1000)
    seq = next(_clord_counter) % 0x1000
    return f"aragon{ms:x}{seq:03x}"


def _fetch_meta_sync(raw: dict) -> InstrumentMeta:
    """rest 元数据 dict → ``InstrumentMeta`` 值对象。"""
    return InstrumentMeta(
        ct_val=raw["ctVal"], ct_val_ccy=raw["ctValCcy"], lot_sz=raw["lotSz"],
        min_sz=raw["minSz"], tick_sz=raw["tickSz"], max_lever=raw["maxLever"],
        ct_type=raw["ctType"], settle_ccy=raw["settleCcy"])


async def _load_meta(inst_id: str) -> InstrumentMeta:
    """取合约元数据（公共 REST，无签名，C1-1）。"""
    rest = get_rest_client()
    raw = await rest.get_instrument_meta(infer_inst_type(inst_id), inst_id)
    return _fetch_meta_sync(raw)


def _ref_price(order: OrderInput, last_px: float | None) -> float:
    """名义 / 上限校验参考价：限价取限价；市价取保守价（买多放大、买空缩小，§2.4）。"""
    if order.ord_type == "limit" and order.px:
        return order.px
    if not last_px:
        return 0.0
    factor = 1 + _MARKET_NOTIONAL_BUFFER if order.direction == "long" else 1 - _MARKET_NOTIONAL_BUFFER
    return last_px * factor


def _would_reduce_net_position(positions: list, inst_id: str, direction: str) -> bool:
    """net_mode 下：本单方向是否与既有净仓相反（会减 / 平既有持仓，C0-1）。"""
    for p in positions:
        if str(p.get("instId")) != inst_id:
            continue
        pos = _optional_float(p.get("pos")) or 0.0
        if pos == 0:
            continue
        if (direction == "long" and pos < 0) or (direction == "short" and pos > 0):
            return True
    return False


def _leverage_pos_side(order: OrderInput, pos_mode: str) -> str | None:
    """set-leverage 的 posSide：仅 isolated + long_short_mode 时随方向传，否则省略（C2-1）。"""
    if order.td_mode == "isolated" and pos_mode == "long_short_mode":
        return "long" if order.direction == "long" else "short"
    return None


def _audit_values(account_id: int, order: OrderInput, cl_ord_id: str, *,
                  status: str, ord_id: str, error_code: str) -> repo.LiveOrderAuditValues:
    """组装审计行（不含任何密钥；时间秒级 UTC，C2）。"""
    return repo.LiveOrderAuditValues(
        account_id=account_id, inst_id=order.inst_id, direction=order.direction,
        ord_type=order.ord_type, td_mode=order.td_mode, px=order.px, sz=order.sz,
        lever=order.lever, tp_trigger_px=order.tp_trigger_px, sl_trigger_px=order.sl_trigger_px,
        status=status, cl_ord_id=cl_ord_id, ord_id=ord_id, error_code=error_code,
        created_ts=int(time.time()))


async def place_live_order(session: AsyncSession, creds: AccountCredentials, account_id: int,
                           order: OrderInput, *, settings: Settings) -> dict:
    """完整下单时序（§2.2）：守门→设杠杆→下单→留痕。失败归一并审计后上抛（绝不假成功）。"""
    cl_ord_id = _gen_cl_ord_id()
    client = OKXTradeClient(creds.api_key, creds.secret_key, creds.passphrase)
    try:
        meta = await _load_meta(order.inst_id)
        ensure_instrument_supported(meta)
        pos_mode = str((await client.get_account_config()).get("posMode") or "long_short_mode")
        positions = await client.get_positions()
        if pos_mode == "net_mode" and _would_reduce_net_position(positions, order.inst_id, order.direction):
            raise OKXPrivateError("LIVE_WOULD_REDUCE_POSITION",
                                  "该单方向与既有净仓相反，会减 / 平现有持仓，已本地拦截")
        last_px = await get_rest_client().get_ticker(order.inst_id)
        validate_order(order, meta, max_notional_usd=settings.live_max_order_notional_usd,
                       ref_px=_ref_price(order, last_px))
        payload = build_order_payload(order, meta, pos_mode, cl_ord_id)
        if order.lever is not None:
            await client.set_leverage(order.inst_id, order.lever, order.td_mode,
                                      _leverage_pos_side(order, pos_mode))
        row = await client.place_order(payload)
    except OKXError as exc:
        mapped = _map_trade_code(exc.code)
        status = "rejected" if mapped in _LOCAL_REJECT_CODES else "error"
        await repo.create_live_order_audit(session, values=_audit_values(
            account_id, order, cl_ord_id, status=status, ord_id="", error_code=mapped))
        raise OKXPrivateError(mapped, _redact(exc.message, creds.secret_key, creds.passphrase)) from exc
    finally:
        await client.aclose()
    ord_id = str(row.get("ordId") or "")
    await repo.create_live_order_audit(session, values=_audit_values(
        account_id, order, cl_ord_id, status="accepted", ord_id=ord_id, error_code=""))
    return {"ok": True, "ordId": ord_id, "clOrdId": str(row.get("clOrdId") or cl_ord_id),
            "code": None, "message": None}


async def set_account_leverage(creds: AccountCredentials, inst_id: str, lever: float,
                               mgn_mode: str, pos_side: str | None = None) -> dict:
    """设杠杆（§4.2）：失败归一 + 脱敏后上抛。"""
    client = OKXTradeClient(creds.api_key, creds.secret_key, creds.passphrase)
    try:
        await client.set_leverage(inst_id, lever, mgn_mode, pos_side)
    except OKXError as exc:
        raise OKXPrivateError(_map_trade_code(exc.code),
                              _redact(exc.message, creds.secret_key, creds.passphrase)) from exc
    finally:
        await client.aclose()
    return {"ok": True, "instId": inst_id, "lever": lever, "mgnMode": mgn_mode, "posSide": pos_side}


async def fetch_live_positions(creds: AccountCredentials) -> list[dict]:
    """真实持仓（§4.4，复用 ``_normalize_position`` 与 OkxAccountPosition 形态）。"""
    client = OKXTradeClient(creds.api_key, creds.secret_key, creds.passphrase)
    try:
        positions = await client.get_positions()
    except OKXError as exc:
        raise OKXPrivateError(_map_trade_code(exc.code),
                              _redact(exc.message, creds.secret_key, creds.passphrase)) from exc
    finally:
        await client.aclose()
    return [_normalize_position(p) for p in positions]


async def fetch_live_fills(creds: AccountCredentials, inst_id: str | None = None,
                           limit: int = 100) -> list[dict]:
    """真实成交明细（§4.5）。"""
    client = OKXTradeClient(creds.api_key, creds.secret_key, creds.passphrase)
    try:
        rows = await client.get_fills(inst_id, limit)
    except OKXError as exc:
        raise OKXPrivateError(_map_trade_code(exc.code),
                              _redact(exc.message, creds.secret_key, creds.passphrase)) from exc
    finally:
        await client.aclose()
    return [_normalize_fill(r) for r in rows]


def _normalize_fill(r: dict) -> dict:
    """OKX trade/fills 单行 → 归一成交（camelCase，秒级 UTC，数值容错）。"""
    return {
        "ts": int(r.get("fillTime") or r.get("ts") or 0) // 1000,
        "instId": str(r.get("instId") or ""),
        "side": str(r.get("side") or ""),
        "fillPx": _optional_float(r.get("fillPx")) or 0.0,
        "fillSz": _optional_float(r.get("fillSz")) or 0.0,
        "fee": _optional_float(r.get("fee")) or 0.0,
        "ordId": str(r.get("ordId") or ""),
    }


async def fetch_trading_config(creds: AccountCredentials, account_id: int, inst_id: str, *,
                               settings: Settings) -> dict:
    """下单面板上下文（§4.1）：posMode / 可用 USDT / 合约元数据 / 最新价 / 标的可下单性。"""
    client = OKXTradeClient(creds.api_key, creds.secret_key, creds.passphrase)
    try:
        config, balance = await asyncio.gather(client.get_account_config(), client.get_balance())
        meta = await _load_meta(inst_id)
        last_px = await get_rest_client().get_ticker(inst_id)
    except OKXError as exc:
        raise OKXPrivateError(_map_trade_code(exc.code),
                              _redact(exc.message, creds.secret_key, creds.passphrase)) from exc
    finally:
        await client.aclose()
    return _build_trading_config(account_id, inst_id, config, balance, meta, last_px, settings)


def _avail_usdt(balance: dict) -> float:
    """从余额 details 取 USDT 可用额（滑块上限用）。"""
    for d in balance.get("details") or []:
        if str(d.get("ccy")) == "USDT":
            return _optional_float(d.get("availBal")) or 0.0
    return 0.0


def _build_trading_config(account_id: int, inst_id: str, config: dict, balance: dict,
                          meta: InstrumentMeta, last_px: float | None,
                          settings: Settings) -> dict:
    """组装 TradingConfigOut（纯数据映射）。"""
    return {
        "accountId": account_id,
        "instId": inst_id,
        "posMode": str(config.get("posMode") or "long_short_mode"),
        "tdMode": settings.live_default_td_mode,
        "availUsdt": _avail_usdt(balance),
        "instMeta": {
            "ctVal": meta.ct_val, "ctValCcy": meta.ct_val_ccy, "lotSz": meta.lot_sz,
            "minSz": meta.min_sz, "tickSz": meta.tick_sz, "maxLever": meta.max_lever,
        },
        "lastPx": last_px,
        "instrumentSupported": is_instrument_supported(meta),
        "liveTradingEnabled": settings.live_trading_enabled,
    }
