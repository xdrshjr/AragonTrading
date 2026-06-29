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
    ClosePositionInput,
    InstrumentMeta,
    OrderInput,
    build_close_order_payload,
    build_order_payload,
    ensure_instrument_supported,
    is_instrument_supported,
    validate_close,
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
# 平仓本地拦截类语义码（P1-1）：须显式纳入透传集合与审计分类，否则会被 accounts._map_okx_code
# 对未知码一律拍平为 OKX_ERROR——既违反「原样回传前端」契约，又使本地拒被误判为 error 而非 rejected。
_CLOSE_LOCAL_REJECT_CODES = {
    "LIVE_CLOSE_REJECTED_LOCAL", "LIVE_CLOSE_SIZE_EXCEEDS", "LIVE_NO_POSITION"}
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
    if (code in _LOCAL_REJECT_CODES or code in _CLOSE_LOCAL_REJECT_CODES
            or code == "LIVE_TRADING_DISABLED"):
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


def _gen_close_cl_ord_id() -> str:
    """生成唯一平仓 clOrdId（前缀 aragonc + 毫秒 hex + 序号；区分开 / 平仓以审计 ord_type 为准，P2-1）。"""
    ms = int(time.time() * 1000)
    seq = next(_clord_counter) % 0x1000
    return f"aragonc{ms:x}{seq:03x}"


def _closed_direction(pos_side: str, pos: float) -> str:
    """被平持仓方向（审计 direction 用，§5）：对冲取 posSide；net 模式按 pos 符号映射。"""
    if pos_side == "net":
        return "long" if pos > 0 else "short"
    return pos_side


def _abs_open_pos(positions: list, inst_id: str, pos_side: str) -> float:
    """实时持仓中该方向的 ``|pos|``（sz 上限的可信来源，绝不信前端 pos，§2.4）。

    对冲模式按 ``(instId, posSide)`` 精确匹配；net 模式（``pos_side=="net"``）按 ``instId`` 匹配。
    """
    total = 0.0
    for p in positions:
        if str(p.get("instId")) != inst_id:
            continue
        if pos_side != "net" and str(p.get("posSide") or "") != pos_side:
            continue
        total += abs(_optional_float(p.get("pos")) or 0.0)
    return total


def _close_audit_values(account_id: int, inst_id: str, direction: str, ord_type: str,
                        mgn_mode: str, px: float | None, sz: float, cl_ord_id: str, *,
                        status: str, ord_id: str, error_code: str) -> repo.LiveOrderAuditValues:
    """组装平仓审计行（复用 live_order_audit；lever / tp / sl 置空，§5；不含任何密钥，C7）。"""
    return repo.LiveOrderAuditValues(
        account_id=account_id, inst_id=inst_id, direction=direction, ord_type=ord_type,
        td_mode=mgn_mode, px=px, sz=sz, lever=None, tp_trigger_px=None, sl_trigger_px=None,
        status=status, cl_ord_id=cl_ord_id, ord_id=ord_id, error_code=error_code,
        created_ts=int(time.time()))


async def close_live_position(session: AsyncSession, creds: AccountCredentials, account_id: int,
                              close: ClosePositionInput) -> dict:
    """单仓平仓时序（§2.4）：守门→取 posMode / 实时 ``|pos|``→（限价校验+place_order / 市价 close_position）→留痕。

    护栏差异（§2.3）：平仓**不**应用名义上限与反向减仓护栏（平仓即减仓，是动作目的）；仅以实时 ``|pos|``
    二次校验 ``sz≤|pos|``（杜绝平成反向仓）。失败经 ``_map_trade_code`` 归一 + ``_redact`` 脱敏后审计上抛
    （本地拒写 ``rejected``、OKX 业务失败写 ``error``），绝不假成功。
    """
    cl_ord_id = _gen_close_cl_ord_id()
    direction = _closed_direction(close.pos_side, close.pos)
    px_audit = close.px if close.ord_type == "limit" else None
    sz_audit = close.sz or 0.0
    client = OKXTradeClient(creds.api_key, creds.secret_key, creds.passphrase)
    try:
        pos_mode = str((await client.get_account_config()).get("posMode") or "long_short_mode")
        abs_pos = _abs_open_pos(await client.get_positions(), close.inst_id, close.pos_side)
        if abs_pos <= 0:
            raise OKXPrivateError("LIVE_NO_POSITION", "该方向当前无可平持仓，无法平仓")
        if close.ord_type == "market":
            row = await client.close_position(close.inst_id, close.pos_side, close.mgn_mode,
                                              auto_cxl=True, cl_ord_id=cl_ord_id)
            ord_id, closed_sz = "", abs_pos
            sz_audit = abs_pos
        else:
            meta = await _load_meta(close.inst_id)
            ensure_instrument_supported(meta)
            sz = validate_close(close, meta, abs_pos)["sz"]
            row = await client.place_order(
                build_close_order_payload(close, meta, pos_mode, sz, cl_ord_id))
            ord_id, closed_sz = str(row.get("ordId") or ""), sz
            sz_audit = sz
    except OKXError as exc:
        mapped = _map_trade_code(exc.code)
        status = "rejected" if mapped in (_LOCAL_REJECT_CODES | _CLOSE_LOCAL_REJECT_CODES) else "error"
        await repo.create_live_order_audit(session, values=_close_audit_values(
            account_id, close.inst_id, direction, close.ord_type, close.mgn_mode,
            px_audit, sz_audit, cl_ord_id, status=status, ord_id="", error_code=mapped))
        raise OKXPrivateError(mapped, _redact(exc.message, creds.secret_key, creds.passphrase)) from exc
    finally:
        await client.aclose()
    await repo.create_live_order_audit(session, values=_close_audit_values(
        account_id, close.inst_id, direction, close.ord_type, close.mgn_mode,
        px_audit, sz_audit, cl_ord_id, status="accepted", ord_id=ord_id, error_code=""))
    return {"ok": True, "mode": close.ord_type, "ordId": ord_id or None,
            "clOrdId": str(row.get("clOrdId") or cl_ord_id), "closedSz": closed_sz,
            "code": None, "message": None}


async def close_all_live_positions(session: AsyncSession, creds: AccountCredentials,
                                   account_id: int) -> dict:
    """一键市价全平时序（§2.5）：取实时持仓→逐仓 ``close_position``→单仓失败并入 ``results`` 不中断全批。

    仅整批前置请求（取持仓，含鉴权 / 连通失败）失败才整体上抛 502；逐仓阶段单仓失败归一 + 脱敏后并入
    结果行并继续。``ok`` 仅在全部成功（``failedCount==0``）为真——诚实汇总，绝不假报「全部成功」（R10）。
    """
    client = OKXTradeClient(creds.api_key, creds.secret_key, creds.passphrase)
    try:
        try:
            positions = await client.get_positions()
        except OKXError as exc:
            raise OKXPrivateError(_map_trade_code(exc.code),
                                  _redact(exc.message, creds.secret_key, creds.passphrase)) from exc
        results: list[dict] = []
        closed = failed = 0
        for p in positions:
            pos = _optional_float(p.get("pos")) or 0.0
            if pos == 0:
                continue
            inst_id = str(p.get("instId") or "")
            pos_side = str(p.get("posSide") or "net")
            mgn_mode = str(p.get("mgnMode") or "cross")
            direction = _closed_direction(pos_side, pos)
            cl_ord_id = _gen_close_cl_ord_id()
            try:
                row = await client.close_position(inst_id, pos_side, mgn_mode,
                                                  auto_cxl=True, cl_ord_id=cl_ord_id)
                await repo.create_live_order_audit(session, values=_close_audit_values(
                    account_id, inst_id, direction, "market", mgn_mode, None, abs(pos),
                    cl_ord_id, status="accepted", ord_id="", error_code=""))
                results.append({"instId": inst_id, "posSide": pos_side, "ok": True,
                                "clOrdId": str(row.get("clOrdId") or cl_ord_id),
                                "code": None, "message": None})
                closed += 1
            except OKXError as exc:
                mapped = _map_trade_code(exc.code)
                await repo.create_live_order_audit(session, values=_close_audit_values(
                    account_id, inst_id, direction, "market", mgn_mode, None, abs(pos),
                    cl_ord_id, status="error", ord_id="", error_code=mapped))
                results.append({"instId": inst_id, "posSide": pos_side, "ok": False,
                                "clOrdId": cl_ord_id, "code": mapped,
                                "message": _redact(exc.message, creds.secret_key, creds.passphrase)})
                failed += 1
    finally:
        await client.aclose()
    return {"ok": failed == 0, "closedCount": closed, "failedCount": failed, "results": results}


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
    """OKX trade/fills 单行 → 归一成交（camelCase，秒级 UTC，数值容错）。

    额外回带 ``clOrdId``（成交 → 自动交易解释的关联主键，下单时我方写入、成交时 OKX 回显）
    与 ``tradeId``（OKX 每笔成交唯一 id，作前端行 key 与展开态稳定身份）。二者仅「停止丢弃」，
    缺失容错为空串，不改变任何外部调用（live-fills-explainability §2.2）。
    """
    return {
        "ts": int(r.get("fillTime") or r.get("ts") or 0) // 1000,
        "instId": str(r.get("instId") or ""),
        "side": str(r.get("side") or ""),
        "fillPx": _optional_float(r.get("fillPx")) or 0.0,
        "fillSz": _optional_float(r.get("fillSz")) or 0.0,
        "fee": _optional_float(r.get("fee")) or 0.0,
        "ordId": str(r.get("ordId") or ""),
        "clOrdId": str(r.get("clOrdId") or ""),
        "tradeId": str(r.get("tradeId") or ""),
    }


async def fetch_live_open_orders(creds: AccountCredentials,
                                 inst_id: str | None = None) -> list[dict]:
    """未结委托（普通挂单 + 策略委托）：聚合归一为统一形态。

    并发查 ``orders-pending``（普通挂单）与 ``orders-algo-pending`` 的 ``oco`` / ``conditional`` 两类
    （随单 TP/SL），按字段展开分类；OKX 不可达 → ``OKXPrivateError``（绝不假数据，C1）。
    """
    client = OKXTradeClient(creds.api_key, creds.secret_key, creds.passphrase)
    try:
        pending, oco, cond = await asyncio.gather(
            client.get_pending_orders(inst_id),
            client.get_algo_pending_orders("oco", inst_id),
            client.get_algo_pending_orders("conditional", inst_id),
        )
    except OKXError as exc:
        raise OKXPrivateError(_map_trade_code(exc.code),
                              _redact(exc.message, creds.secret_key, creds.passphrase)) from exc
    finally:
        await client.aclose()
    out: list[dict] = [_normalize_pending_order(r) for r in pending]
    for r in (*oco, *cond):
        out.extend(_normalize_algo_order(r))   # 一条 OCO 可展开成 tp + sl 两条
    return out


def _as_bool(value: object) -> bool:
    """OKX 布尔字段（可能是 bool 或 "true"/"false" 字符串）→ Python bool。"""
    return value is True or str(value).lower() == "true"


def _algo_px(value: object) -> float | None:
    """策略委托委托价：``"-1"``（触发后市价）/ 空 → ``None``，否则数值（§2.2b）。"""
    text = str(value or "").strip()
    if text in ("", "-1"):
        return None
    return _optional_float(text)


def _classify_pending_kind(r: dict) -> str:
    """普通挂单 kind 判定（R4）：reduceOnly 或对冲反向单 → ``limit_close``，否则 ``limit_open``。"""
    if _as_bool(r.get("reduceOnly")):
        return "limit_close"
    pos_side = str(r.get("posSide") or "")
    side = str(r.get("side") or "")
    if (pos_side == "long" and side == "sell") or (pos_side == "short" and side == "buy"):
        return "limit_close"
    return "limit_open"


def _normalize_pending_order(r: dict) -> dict:
    """OKX orders-pending 单行 → 归一未结委托（camelCase，秒级 UTC，数值容错）。"""
    return {
        "kind": _classify_pending_kind(r),
        "instId": str(r.get("instId") or ""),
        "posSide": str(r.get("posSide") or "net"),
        "side": str(r.get("side") or ""),
        "px": _optional_float(r.get("px")),
        "triggerPx": None,
        "sz": _optional_float(r.get("sz")),
        "state": str(r.get("state") or ""),
        "reduceOnly": _as_bool(r.get("reduceOnly")),
        "ordId": str(r.get("ordId") or "") or None,
        "algoId": None,
        "createdTs": int(r.get("cTime") or 0) // 1000,
    }


def _normalize_algo_order(r: dict) -> list[dict]:
    """OKX orders-algo-pending 单行 → 归一未结委托列表。

    一条策略委托可同时含 ``tpTriggerPx`` 与 ``slTriggerPx``（OCO），分别展开为 ``tp`` / ``sl`` 两条
    归一记录并共享 ``algoId``；只含其一则产出一条（不依赖 ordType 名，对 oco/conditional 命名稳健）。
    """
    base = {
        "instId": str(r.get("instId") or ""),
        "posSide": str(r.get("posSide") or "net"),
        "side": str(r.get("side") or ""),
        "sz": _optional_float(r.get("sz")),
        "state": str(r.get("state") or ""),
        "reduceOnly": _as_bool(r.get("reduceOnly")),
        "ordId": None,
        "algoId": str(r.get("algoId") or "") or None,
        "createdTs": int(r.get("cTime") or 0) // 1000,
    }
    out: list[dict] = []
    tp_trigger = _optional_float(r.get("tpTriggerPx"))
    if tp_trigger is not None:
        out.append({**base, "kind": "tp", "triggerPx": tp_trigger,
                    "px": _algo_px(r.get("tpOrdPx"))})
    sl_trigger = _optional_float(r.get("slTriggerPx"))
    if sl_trigger is not None:
        out.append({**base, "kind": "sl", "triggerPx": sl_trigger,
                    "px": _algo_px(r.get("slOrdPx"))})
    return out


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
