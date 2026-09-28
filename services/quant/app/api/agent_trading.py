"""账户绑定与交易卡 REST；格式错误只回中文受控字段。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError

from ..agent_trading.binding import (
    BindingService,
    binding_view,
    current_environment,
)
from ..agent_trading.errors import TradingError
from ..agent_trading.read_service import list_accounts
from ..agent_trading.result_view import current_intent_view, intent_view
from ..agent_trading.schemas import (
    DismissRequest,
    ExecuteRequest,
    parse_request,
)
from ..config import get_settings
from ..db.agent_trade_repositories import AgentTradeRepository, TERMINAL
from ..db.session import get_sessionmaker


class TradingRoute(APIRoute):
    """局部错误边界，不改旧 REST 接口格式。"""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            try:
                return await original(request)
            except TradingError as exc:
                return JSONResponse(exc.as_dict(), status_code=exc.status_code)
            except (RequestValidationError, json.JSONDecodeError, ValueError):
                exc = TradingError("INVALID_TRADING_REQUEST")
                return JSONResponse(exc.as_dict(), status_code=400)
            except SQLAlchemyError:
                exc = TradingError("STATE_STORE_UNAVAILABLE")
                return JSONResponse(exc.as_dict(), status_code=503)

        return handle


router = APIRouter(
    prefix="/agent", tags=["agent-trading"], route_class=TradingRoute
)


def _repo():
    return AgentTradeRepository(get_sessionmaker())


async def _find(cid, intent_id):
    row = await _repo().get_intent(intent_id, cid)
    if not row:
        raise TradingError("INTENT_NOT_FOUND")
    return row


@router.get("/trading/capabilities")
async def capabilities():
    """模型未配置时也可读取能力；执行由配置和恢复屏障共同决定。"""
    from ..agent_trading.admission import is_ready

    settings = get_settings()
    enabled = getattr(settings, "agent_trading_enabled", False)
    return {
        "enabled": enabled,
        "executionEnabled": bool(
            enabled
            and is_ready()
            and getattr(settings, "agent_trading_execution_enabled", False)
            and settings.live_trading_enabled
        ),
        "recoveryReady": is_ready(),
        "environment": current_environment(),
        "supportedActions": ["open", "close", "cancel"],
        "accounts": await list_accounts(get_sessionmaker()),
    }


async def _list(cid, request):
    limit = int(request.query_params.get("limit", "20"))
    if not 1 <= limit <= 50:
        raise TradingError("INVALID_TRADING_REQUEST")
    rows, cursor = await _repo().list_intents(
        cid, request.query_params.get("cursor"), limit
    )
    items = [
        await current_intent_view(
            await _repo().get_intent(row.id, cid), get_sessionmaker()
        )
        for row in rows
    ]
    return {"items": items, "nextCursor": cursor}


@router.get("/trading/unsettled-intents")
async def unsettled(request: Request):
    """单用户全局未结只读入口，功能关闭后仍保留。"""
    return await _list(None, request)


@router.get("/conversations/{cid}/trading-binding")
async def get_binding(cid: str):
    """读取当前会话的持久绑定。"""
    return binding_view(await BindingService(get_sessionmaker()).get(cid))


@router.put("/conversations/{cid}/trading-binding")
async def put_binding(cid: str, request: Request):
    """修改账户或模式必须提交 expectedVersion。"""
    return binding_view(
        await BindingService(get_sessionmaker()).put(cid, await request.json())
    )


@router.get("/conversations/{cid}/trade-intents")
async def list_intents(cid: str, request: Request):
    """倒序读取会话交易历史，不依赖消息是否已清空。"""
    return await _list(cid, request)


@router.get("/conversations/{cid}/trade-intents/{intent_id}")
async def get_intent(cid: str, intent_id: str):
    """读取持久状态，GET 不触发交易。"""
    return await current_intent_view(
        await _find(cid, intent_id), get_sessionmaker()
    )


@router.post("/conversations/{cid}/trade-intents/{intent_id}/execute")
async def execute(cid: str, intent_id: str, request: Request):
    """浏览器授权只携带版本及预览散列，不接收任何订单覆盖。"""
    from ..agent_trading.execution import execute_intent

    params = parse_request(ExecuteRequest, await request.json())
    await _find(cid, intent_id)
    row = await execute_intent(get_sessionmaker(), cid, intent_id, params)
    return JSONResponse(
        intent_view(row), status_code=200 if row.status in TERMINAL else 202
    )


@router.post("/conversations/{cid}/trade-intents/{intent_id}/dismiss")
async def dismiss(cid: str, intent_id: str, request: Request):
    """只废弃未提交预览；绝不撤销交易所订单。"""
    params = parse_request(DismissRequest, await request.json())
    row = await _find(cid, intent_id)
    if row.status == "dismissed":
        return intent_view(row)
    if row.status != "prepared":
        raise TradingError("INTENT_ALREADY_SUBMITTED")
    return intent_view(
        await _repo().transition(row.id, params.version, "dismissed")
    )
