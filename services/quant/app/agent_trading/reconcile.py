"""持久未结意图只读核对；重启与孤儿恢复绝不重发。"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from decimal import Decimal, InvalidOperation

from ..config import get_settings
from ..db.agent_trade_repositories import AgentTradeRepository
from ..okx.trade_client import OKXTradeClient
from .errors import TradingError

logger = logging.getLogger(__name__)
_task: asyncio.Task | None = None


def reconcile_state(action, current, exchange_state):
    """撤单的非终态不能证明撤销请求已被交易所受理。"""
    terminal = {"filled": "filled", "canceled": "canceled", "mmp_canceled": "canceled"}
    if exchange_state in terminal:
        return terminal[exchange_state]
    if action == "cancel":
        return current
    return {"live": "accepted", "partially_filled": "partially_filled"}.get(
        exchange_state, current)


async def recover_startup(session_factory) -> int:
    """启动恢复只修改数据库；就绪屏障由 main 的联合恢复打开。"""
    from .admission import set_ready
    set_ready(False)
    return await AgentTradeRepository(session_factory).recover_submitting()


def start(session_factory, recovery_probe=None):
    """启动强引用单例核对任务，禁用新交易时仍持续查询旧订单。"""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_run(session_factory, recovery_probe))
    return _task


async def stop() -> None:
    """等待核对任务停止；不会更改未结意图的业务状态。"""
    global _task
    task, _task = _task, None
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _run(session_factory, recovery_probe):
    while True:
        try:
            await reconcile_batch(session_factory)
            from . import admission
            if (recovery_probe is not None and not admission.is_ready()
                    and not admission.is_closing()):
                await recover_orphans(session_factory)
                recovered = await asyncio.wait_for(recovery_probe(), timeout=15)
                if recovered and not admission.is_closing():
                    admission.set_ready(True)
        except Exception:
            from .admission import set_ready
            set_ready(False)
            logger.error("未结交易核对失败，保持写入关闭")
        await asyncio.sleep(5)


async def recover_orphans(session_factory) -> None:
    """运行中恢复只转换没有活跃 owner 的 submitting，不抢活跃发送。"""
    from .execution import has_owner
    repository = AgentTradeRepository(session_factory)
    for row in await repository.list_unsettled():
        if row.status == "submitting" and not has_owner(row.id):
            await repository.transition(row.id, row.version, "unknown")


async def reconcile_batch(session_factory) -> None:
    """每批十条、最多两个并发，每个查询十秒期限。"""
    from .execution import has_owner
    repository = AgentTradeRepository(session_factory)
    rows = await repository.list_unsettled()
    due = [row for row in rows if not has_owner(row.id)
           and (row.next_check_ts or 0) <= time.time()][:10]
    semaphore = asyncio.Semaphore(2)
    async def run_one(row):
        async with semaphore:
            await reconcile_one(repository, row)
    await asyncio.gather(*(run_one(row) for row in due))


async def reconcile_one(repository, row):
    """身份核实失败只记录阻塞原因，不能拿新环境查询旧单。"""
    from .execution import has_owner
    if has_owner(row.id):
        return row
    if row.status == "submitting":
        row = await repository.transition(row.id, row.version, "unknown")
    try:
        result = await asyncio.wait_for(_fetch(repository, row), timeout=10)
        return await _apply(repository, row, result)
    except Exception as exc:
        if isinstance(exc, TradingError) and exc.code == "INTENT_VERSION_CONFLICT":
            return await repository.get_intent(row.id)
        attempts = row.check_attempts + 1
        return await repository.touch(row.id, row.version, {
            "check_attempts": attempts,
            "next_check_ts": int(time.time()) + (60 if attempts >= 12 else 5),
            "error_code": getattr(exc, "code", "ACCOUNT_READ_FAILED")})


async def _fetch(repository, row):
    from .binding import resolve_account
    from ..okx.trading import _drop_positions_cache
    identity = await resolve_account(row.account_id, repository.session_factory)
    if identity.account_id != row.account_id:
        raise TradingError("IDENTITY_MISMATCH")
    if identity.environment != row.environment:
        raise TradingError("ENVIRONMENT_CHANGED")
    if identity.credential_fingerprint != row.credential_fingerprint:
        raise TradingError("ACCOUNT_CREDENTIALS_CHANGED")
    creds = identity.credentials
    client = OKXTradeClient(creds.api_key, creds.secret_key, creds.passphrase,
                            settings=get_settings().model_copy(deep=True))
    try:
        body = json.loads(row.preview_json)["payload"]
        old = json.loads(row.result_json or "{}")
        ord_id = row.target_ord_id if row.action == "cancel" else old.get("ordId")
        query = {"ord_id": ord_id} if ord_id else {"cl_ord_id": row.cl_ord_id}
        result = await client.get_order(body["instId"], **query)
        if not result:
            raise TradingError("ORDER_NOT_FOUND")
        _check_order_identity(row, result, ord_id)
        if (result.get("state") in {"filled", "canceled", "mmp_canceled"}
                or Decimal(str(result.get("accFillSz") or 0))
                > Decimal(str(old.get("accFillSz") or 0))):
            _drop_positions_cache(client)
        return result
    finally:
        await client.aclose()


def _check_order_identity(row, result, ord_id):
    preview = json.loads(row.preview_json)
    body = preview["payload"]
    if (str(result.get("instId") or "") != body["instId"]
            or not result.get("ordId")
            or (ord_id and str(result["ordId"]) != ord_id)):
        raise TradingError("IDENTITY_MISMATCH")
    if row.action != "cancel" and str(result.get("clOrdId") or "") != row.cl_ord_id:
        raise TradingError("IDENTITY_MISMATCH")
    expected = dict(preview.get("targetSnapshot", {}) if row.action == "cancel" else body)
    if row.action != "cancel":
        # 净仓下单省略 posSide，但查单仍必须证明返回的是同一净仓订单。
        expected.setdefault("posSide", "net")
    for key in ("side", "posSide", "sz", "tdMode", "ordType"):
        if key not in expected:
            continue
        actual = result.get(key)
        wanted = expected[key]
        if key == "posSide":
            actual, wanted = actual or "net", wanted or "net"
        if key == "sz":
            if _decimal(actual) == _decimal(wanted):
                continue
            raise TradingError("IDENTITY_MISMATCH")
        if str(actual) != str(wanted):
            raise TradingError("IDENTITY_MISMATCH")


def _decimal(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise TradingError("ACCOUNT_READ_FAILED") from None
    if not number.is_finite() or number < 0:
        raise TradingError("ACCOUNT_READ_FAILED")
    return number


def _validate_fill(row, result):
    preview = json.loads(row.preview_json)
    expected = preview.get("targetSnapshot", {}) if row.action == "cancel" else preview["payload"]
    fill, price = result.get("accFillSz"), result.get("avgPx")
    if fill not in (None, ""):
        size = _decimal(fill)
        if expected.get("sz") is not None and size > _decimal(expected["sz"]):
            raise TradingError("IDENTITY_MISMATCH")
    if price not in (None, "") and _decimal(price) <= 0:
        raise TradingError("ACCOUNT_READ_FAILED")


async def _apply(repository, row, result):
    _validate_fill(row, result)
    state = str(result.get("state") or "")
    target = reconcile_state(row.action, row.status, state)
    old = json.loads(row.result_json or "{}")
    evidence = dict(old, ordId=str(result.get("ordId") or ""),
                    exchangeState=state,
                    accFillSz=str(result["accFillSz"]) if result.get("accFillSz")
                    not in (None, "") else old.get("accFillSz"),
                    avgPx=result.get("avgPx") or None,
                    lastCheckedTs=int(time.time()))
    if row.action == "cancel" and target == "filled":
        evidence["message"] = "撤销前已成交"
    known = state in {"live", "partially_filled", "filled", "canceled", "mmp_canceled"}
    attempts = 0 if known and target != "unknown" else row.check_attempts + 1
    changes = {"result_json": json.dumps(evidence), "check_attempts": attempts,
               "error_code": None, "next_check_ts": int(time.time())
               + (60 if attempts >= 12 else 5)}
    if target != row.status or any(evidence.get(key) != old.get(key)
                                  for key in ("accFillSz", "avgPx")):
        return await repository.transition(row.id, row.version, target, changes)
    return await repository.touch(row.id, row.version, changes)
