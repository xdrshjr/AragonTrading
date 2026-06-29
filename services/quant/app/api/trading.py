"""路由：实盘交易 REST（下单 / 设杠杆 / 配置 / 持仓 / 成交）(实盘交易模式 §4)。

写类接口（order / leverage）统一受 ``LIVE_TRADING_ENABLED`` 全局开关守门：关闭即 ``403
{code:"LIVE_TRADING_DISABLED"}``，先于一切凭证读取（C1-3 安全护栏）。读类接口（config / positions /
fills）不受该开关限制（仅查不写）。

错误体统一 `{error:{code,message}}`：边界错误（404/400/403/422）经 ``main`` 全局 HTTPException 处理器
包装；OKX 鉴权 / 业务 / 本地拦截失败由 ``OKXPrivateError`` 经全局 ``okx_error_handler`` 统一回 502。
密钥仅服务层构造客户端用，绝不出接口 / 不进日志（C7）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..db import repositories as repo
from ..db.session import get_session
from ..live_auto.fills_explain import attach_explains, index_trade_explains
from ..okx.accounts import AccountCredentials
from ..okx.order_builder import ClosePositionInput, OrderInput
from ..okx.trading import (
    close_all_live_positions,
    close_live_position,
    fetch_live_fills,
    fetch_live_open_orders,
    fetch_live_positions,
    fetch_trading_config,
    place_live_order,
    set_account_leverage,
)
from ..schemas import (
    CloseAllIn,
    CloseAllOut,
    ClosePositionIn,
    ClosePositionOut,
    LiveFillsOut,
    LiveOpenOrdersOut,
    LivePositionsOut,
    PlaceOrderIn,
    PlaceOrderOut,
    SetLeverageIn,
    SetLeverageOut,
    TradingConfigOut,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["trading"], prefix="/trading")

# 成交 → 解释关联的时间窗 slack（秒，RV1/P0）：自动交易 trade 落库 ts 是确认根开盘时刻，
# 真实成交在收线之后约一个 bar 周期才发生（最大 1D=86400s，见 okx.types.BAR_SECONDS）。
# slack 须 ≥ 最大 bar 周期 + 余量（取 90000，约 25h），否则除 1m 外所有周期都会被 ts 下界整段筛掉。
_FILL_JOIN_SLACK_SEC = 90000
# 解释行查询的硬上限（真正的扫描硬边界）：OKX fills 仅回近三日 ≤100 行、解释行本就低频，
# 时间窗只作粗筛、limit 兜底，二者叠加即小结果集。
_FILL_JOIN_MAX = 1000


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
    """真实成交明细（轮询源），best-effort 富化自动交易触发解释（live-fills-explainability §2.4）。

    取真实 OKX fills 后，按其时间窗查本地解释行 → 建 clOrdId/ordId 索引 → 逐 fill 关联挂 explain；
    整段 try/except 隔离：任何异常仅 warning 并原样返回未富化 fills（成交永不因解释失败而不可见，R1）。
    """
    row = await _resolve_account(session, accountId)
    fills = await fetch_live_fills(_creds_from_row(row), instId, limit)
    if not fills:
        return {"fills": fills}                      # 无成交：免去多余 DB 读（RV3）
    try:
        since_ts = min(f["ts"] for f in fills) - _FILL_JOIN_SLACK_SEC
        explained = await repo.list_explained_trades(session, since_ts=since_ts,
                                                     limit=_FILL_JOIN_MAX)
        fills = attach_explains(fills, index_trade_explains(explained))
    except Exception as exc:    # noqa: BLE001 —— 解释富化失败绝不阻断成交可见
        logger.warning("attach fills explain failed (account=%s): %s", row.id, exc)
    return {"fills": fills}


@router.get("/open-orders", response_model=LiveOpenOrdersOut)
async def trading_open_orders(accountId: int | None = Query(None),
                              instId: str | None = Query(None),
                              session: AsyncSession = Depends(get_session)) -> dict:
    """未结委托（普通挂单 + 策略委托）：真实 OKX，轮询源，鉴权失败→502（读类不守门）。

    ``instId`` 缺省返回全量（前端不传、客户端按 instId 过滤）；传入则服务端透传过滤（C5）。
    """
    row = await _resolve_account(session, accountId)
    return {"orders": await fetch_live_open_orders(_creds_from_row(row), instId)}


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


@router.post("/close", response_model=ClosePositionOut)
async def trading_close(body: ClosePositionIn, session: AsyncSession = Depends(get_session),
                        settings: Settings = Depends(get_settings)) -> dict:
    """实盘平仓（limit 挂单 / market 整仓；受 LIVE_TRADING_ENABLED 守门；clOrdId 后端生成，每次留痕）。"""
    _require_live_enabled(settings)
    row = await _resolve_account(session, body.accountId)
    close = ClosePositionInput(
        inst_id=body.instId, pos_side=body.posSide, pos=body.pos, mgn_mode=body.mgnMode,
        ord_type=body.ordType, px=body.px, sz=body.sz)
    return await close_live_position(session, _creds_from_row(row), row.id, close)


@router.post("/close-all", response_model=CloseAllOut)
async def trading_close_all(body: CloseAllIn, session: AsyncSession = Depends(get_session),
                            settings: Settings = Depends(get_settings)) -> dict:
    """一键市价全平（受 LIVE_TRADING_ENABLED 守门；逐仓诚实汇总，单仓失败不致整体失败，R10）。"""
    _require_live_enabled(settings)
    row = await _resolve_account(session, body.accountId)
    return await close_all_live_positions(session, _creds_from_row(row), row.id)
