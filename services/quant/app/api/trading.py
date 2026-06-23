"""路由：实盘交易 REST（下单 / 设杠杆 / 配置 / 持仓 / 成交）(实盘交易模式 §4)。

写类接口（order / leverage）统一受 ``LIVE_TRADING_ENABLED`` 全局开关守门：关闭即 ``403
{code:"LIVE_TRADING_DISABLED"}``，先于一切凭证读取（C1-3 安全护栏）。读类接口（config / positions /
fills）不受该开关限制（仅查不写）。

错误体统一 `{error:{code,message}}`：边界错误（404/400/403/422）经 ``main`` 全局 HTTPException 处理器
包装；OKX 鉴权 / 业务 / 本地拦截失败由 ``OKXPrivateError`` 经全局 ``okx_error_handler`` 统一回 502。
密钥仅服务层构造客户端用，绝不出接口 / 不进日志（C7）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..db import repositories as repo
from ..db.session import get_session
from ..okx.accounts import AccountCredentials
from ..okx.order_builder import OrderInput
from ..okx.trading import (
    fetch_live_fills,
    fetch_live_positions,
    fetch_trading_config,
    place_live_order,
    set_account_leverage,
)
from ..schemas import (
    LiveFillsOut,
    LivePositionsOut,
    PlaceOrderIn,
    PlaceOrderOut,
    SetLeverageIn,
    SetLeverageOut,
    TradingConfigOut,
)

router = APIRouter(tags=["trading"], prefix="/trading")


def _creds_from_row(row: repo.OkxAccount) -> AccountCredentials:
    """用库内明文密钥构造凭证（仅服务层内构造客户端用，绝不出接口）。"""
    return AccountCredentials(api_key=row.api_key, secret_key=row.secret_key,
                              passphrase=row.passphrase)


def _require_live_enabled(settings: Settings) -> None:
    """写类接口守门：实盘开关关闭即 403（先于凭证读取，C1-3）。"""
    if not settings.live_trading_enabled:
        raise HTTPException(status_code=403,
                            detail={"code": "LIVE_TRADING_DISABLED", "message": "实盘下单未启用"})


async def _resolve_account(session: AsyncSession, account_id: int | None) -> repo.OkxAccount:
    """取指定账户或回落默认账户；缺失 → 404，凭证不全 → 400（绝不假数据）。"""
    if account_id is not None:
        row = await repo.get_okx_account(session, account_id)
    else:
        row = await repo.get_default_okx_account(session)
    if row is None:
        raise HTTPException(status_code=404,
                            detail={"code": "ACCOUNT_NOT_FOUND", "message": "账户不存在或未配置默认账户"})
    if not ((row.secret_key or "").strip() and (row.passphrase or "").strip()):
        raise HTTPException(status_code=400,
                            detail={"code": "ACCOUNT_NOT_CONFIGURED",
                                    "message": "请先补全 Secret Key / Passphrase"})
    return row


@router.get("/config", response_model=TradingConfigOut)
async def trading_config(instId: str = Query(...), accountId: int | None = Query(None),
                         session: AsyncSession = Depends(get_session),
                         settings: Settings = Depends(get_settings)) -> dict:
    """下单面板上下文（真实 OKX；鉴权失败→502，绝不假数据）。"""
    row = await _resolve_account(session, accountId)
    return await fetch_trading_config(_creds_from_row(row), row.id, instId, settings=settings)


@router.get("/positions", response_model=LivePositionsOut)
async def trading_positions(accountId: int | None = Query(None),
                            session: AsyncSession = Depends(get_session)) -> dict:
    """真实持仓（轮询源）。"""
    row = await _resolve_account(session, accountId)
    return {"positions": await fetch_live_positions(_creds_from_row(row))}


@router.get("/fills", response_model=LiveFillsOut)
async def trading_fills(accountId: int | None = Query(None), instId: str | None = Query(None),
                        limit: int = Query(100, ge=1, le=100),
                        session: AsyncSession = Depends(get_session)) -> dict:
    """真实成交明细（轮询源）。"""
    row = await _resolve_account(session, accountId)
    return {"fills": await fetch_live_fills(_creds_from_row(row), instId, limit)}


@router.post("/leverage", response_model=SetLeverageOut)
async def trading_leverage(body: SetLeverageIn, session: AsyncSession = Depends(get_session),
                           settings: Settings = Depends(get_settings)) -> dict:
    """设杠杆（受 LIVE_TRADING_ENABLED 守门）。"""
    _require_live_enabled(settings)
    row = await _resolve_account(session, body.accountId)
    return await set_account_leverage(_creds_from_row(row), body.instId, body.lever,
                                      body.mgnMode, body.posSide)


@router.post("/order", response_model=PlaceOrderOut)
async def trading_order(body: PlaceOrderIn, session: AsyncSession = Depends(get_session),
                        settings: Settings = Depends(get_settings)) -> dict:
    """实盘下单（受 LIVE_TRADING_ENABLED 守门；clOrdId 由后端生成，每单留痕审计）。"""
    _require_live_enabled(settings)
    row = await _resolve_account(session, body.accountId)
    order = OrderInput(
        inst_id=body.instId, direction=body.direction, ord_type=body.ordType,
        td_mode=body.tdMode, px=body.px, sz=body.sz, lever=body.lever,
        tp_trigger_px=body.tpTriggerPx, sl_trigger_px=body.slTriggerPx)
    return await place_live_order(session, _creds_from_row(row), row.id, order, settings=settings)
