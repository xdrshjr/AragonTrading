"""路由：实时自动交易 start/stop/status + ws /ws/live-auto/{sid}（live-auto-trading §4）。

``start`` 受 ``LIVE_AUTO_TRADING_ENABLED`` **与** ``LIVE_TRADING_ENABLED`` 双闸守门
（R1：自动路径直接调服务层、绕开 trading 路由的 ``_require_live_enabled``，故须在此显式同时校验，
任一为 false → 403 ``LIVE_AUTO_TRADING_DISABLED``，先于一切凭证读取）。``stop`` **刻意不守门**：
即便运维中途关闭总闸，也须保证既有会话随时可停止并平掉自管持仓（安全优先于一致性）。
``GET /status`` 只读不守门。错误体统一 ``{error:{code,message}}``（经 main 全局 HTTPException 处理器包装）。
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..db import repositories as repo
from ..db.session import get_session
from ..live_auto.manager import (
    GroupMemberInput,
    GroupStartRequest,
    LiveAutoGroupPreflightError,
    LiveAutoPreflightError,
    LiveAutoSessionLimitError,
    get_live_auto_manager,
)
from ..paper.strategy_factory import build_paper_strategy_spec
from ..schemas import LiveAutoGroupStartIn, LiveAutoStartIn, run_out
from .ws_guard import guard_ws_origin

router = APIRouter(tags=["live-auto"])

# 静默此秒数后下发 ping（半开探测，镜像 ws_weight_train / ws_ai_backtest）。盯市 tick 今天恰好每 5s
# 推一次 equity 掩盖了这个问题，但一旦会话进终态或行情断流，它立刻退化成每 46 秒一次的假断连。
_WS_HEARTBEAT_SECONDS = 20.0


def _require_auto_enabled(settings: Settings) -> None:
    """写类守门（R1）：自动化总闸与底层写链路总闸须同时为 True，否则 403（先于凭证读取）。"""
    if not (settings.live_auto_trading_enabled and settings.live_trading_enabled):
        raise HTTPException(status_code=403, detail={
            "code": "LIVE_AUTO_TRADING_DISABLED",
            "message": "实时自动交易未启用（需 LIVE_AUTO_TRADING_ENABLED 与 LIVE_TRADING_ENABLED 均开启）"})


async def _resolve_account(session: AsyncSession, account_id: int | None) -> repo.OkxAccount:
    """取指定账户或回落默认账户；缺失 → 404，凭证不全 → 400（绝不假数据，对齐 trading 路由）。"""
    if account_id is not None:
        row = await repo.get_okx_account(session, account_id)
    else:
        row = await repo.get_default_okx_account(session)
    if row is None:
        raise HTTPException(status_code=404, detail={
            "code": "ACCOUNT_NOT_FOUND", "message": "账户不存在或未配置默认账户"})
    if not ((row.secret_key or "").strip() and (row.passphrase or "").strip()):
        raise HTTPException(status_code=400, detail={
            "code": "ACCOUNT_NOT_CONFIGURED", "message": "请先补全 Secret Key / Passphrase"})
    return row


@router.post("/live-auto/start")
async def live_auto_start(body: LiveAutoStartIn, session: AsyncSession = Depends(get_session),
                          settings: Settings = Depends(get_settings)) -> dict:
    """启动实时自动交易（受双闸守门；§2.4 前置门禁；回执 budgetUsd 为夹紧后最终生效预算）。"""
    _require_auto_enabled(settings)
    account_row = await _resolve_account(session, body.accountId)
    strategy_row = await repo.get_strategy_row(session, body.strategyId)
    if strategy_row is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "策略不存在"})
    strategy_spec = build_paper_strategy_spec(strategy_row)
    try:
        sess = await get_live_auto_manager().start(
            strategy_spec=strategy_spec, inst_id=body.instId, bar=body.bar,
            account_row=account_row, settings=settings, capital_usd=body.capitalUsd,
            leverage=body.leverage, take_profit_usd=body.takeProfitUsd,
            stop_loss_usd=body.stopLossUsd, priming=body.priming, priming_bars=body.primingBars)
    except (LiveAutoSessionLimitError, LiveAutoPreflightError) as exc:
        raise HTTPException(exc.status, detail={"code": exc.code, "message": exc.message})
    return {
        "sessionId": sess.session_id, "runId": sess.run_id,
        "strategyKind": strategy_spec.strategy_kind, "instId": body.instId, "bar": body.bar,
        "budgetUsd": sess.executor.budget_usd, "leverage": sess.executor.leverage}


@router.post("/live-auto/{session_id}/stop")
async def live_auto_stop(session_id: str,
                         session: AsyncSession = Depends(get_session)) -> dict:
    """平掉自管持仓 + 停会话 + 落 Run 终态（刻意不守门，确保任何时候可停止平仓）。"""
    result = await get_live_auto_manager().stop(session_id)
    if result is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "会话不存在"})
    sess, flattened = result
    run = await repo.get_run(session, sess.run_id)
    return {"sessionId": session_id, "stopReason": sess.stop_reason,
            "run": run_out(run) if run is not None else None, "flattened": flattened}


@router.get("/live-auto/status")
async def live_auto_status(settings: Settings = Depends(get_settings)) -> dict:
    """当前活跃自动会话（无则 session:null）；前端重载后据此重连 WS（只读，不守门）。"""
    return {"session": get_live_auto_manager().status_row(),
            "liveAutoTradingEnabled": settings.live_auto_trading_enabled}


@router.post("/live-auto/group/start")
async def live_auto_group_start(body: LiveAutoGroupStartIn,
                                session: AsyncSession = Depends(get_session),
                                settings: Settings = Depends(get_settings)) -> dict:
    """启动多标的自动交易分组（受双闸守门；两段式前置门禁 + 整组回滚，§2.4 / §4.1）。"""
    _require_auto_enabled(settings)
    account_row = await _resolve_account(session, body.accountId)
    strategy_row = await repo.get_strategy_row(session, body.strategyId)
    if strategy_row is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "策略不存在"})
    strategy_spec = build_paper_strategy_spec(strategy_row)
    # 服务端为分组预算上限唯一权威（RN-1 / R3）：从 DB 读用户配置的总预算上限百分比，夹到分组运营天花板
    # 得有效上限分数；留空默认 = 上限本身（不随成员数缩放）。**不**接受前端任何「上限」入参。
    at_settings = await repo.get_auto_trade_settings(session)
    cap_frac = min(at_settings["maxBudgetPct"] / 100.0, settings.live_auto_group_max_capital_frac)
    req = GroupStartRequest(
        strategy_spec=strategy_spec, bar=body.bar,
        members_in=[GroupMemberInput(inst_id=m.instId, allocation_pct=m.allocationPct)
                    for m in body.members],
        account_row=account_row, settings=settings, total_capital_usd=body.totalCapitalUsd,
        leverage=body.leverage, take_profit_usd=body.takeProfitUsd,
        stop_loss_usd=body.stopLossUsd, priming=body.priming, priming_bars=body.primingBars,
        budget_cap_frac=cap_frac, default_budget_frac=cap_frac,
        allocation_mode=body.allocationMode)
    try:
        group = await get_live_auto_manager().start_group(req)
    except LiveAutoGroupPreflightError as exc:
        # P1-4：整组前置失败须透传逐标的 members 明细（全局处理器据此包成 {error:{code,message,members}}）。
        raise HTTPException(exc.status, detail={
            "code": exc.code, "message": exc.message, "members": exc.members})
    except (LiveAutoSessionLimitError, LiveAutoPreflightError) as exc:
        raise HTTPException(exc.status, detail={"code": exc.code, "message": exc.message})
    return {
        "groupId": group.group_id, "strategyId": group.strategy_id,
        "strategyName": group.strategy_name, "strategyKind": group.strategy_kind,
        "bar": group.bar, "totalBudgetUsd": group.total_budget_usd, "leverage": group.leverage,
        "members": [{"instId": m.inst_id, "sessionId": m.session.session_id, "runId": m.run_id,
                     "allocationPct": m.allocation_pct, "budgetUsd": m.budget_usd,
                     "state": m.session.state} for m in group.members]}


@router.post("/live-auto/group/{group_id}/stop")
async def live_auto_group_stop(group_id: str) -> dict:
    """停整组（停所有成员、各自平自管仓、落 Run 终态；刻意不守门，确保随时可停止平仓）。"""
    result = await get_live_auto_manager().stop_group(group_id)
    if result is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "分组不存在"})
    return result


@router.post("/live-auto/group/{group_id}/member/{inst_id}/stop")
async def live_auto_group_member_stop(group_id: str, inst_id: str) -> dict:
    """仅停某成员（其余续跑；不守门）；停后无运行成员则分组清空。"""
    result = await get_live_auto_manager().stop_member(group_id, inst_id)
    if result is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "分组或成员不存在"})
    return result


@router.get("/live-auto/group/status")
async def live_auto_group_status(settings: Settings = Depends(get_settings)) -> dict:
    """当前活跃分组（无则 group:null）；前端重载后据此重连各成员 WS（只读，不守门）。"""
    return {"group": get_live_auto_manager().group_status(),
            "liveAutoTradingEnabled": settings.live_auto_trading_enabled}


@router.websocket("/ws/live-auto/{session_id}")
async def ws_live_auto(websocket: WebSocket, session_id: str) -> None:
    """事件协议与 /ws/paper/{id} 同构：连接即原子 subscribe→status→snapshot（防丢事件）。"""
    if not await guard_ws_origin(websocket):   # 必须在 accept() 之前：不合规连接一帧都不许收到
        return
    await websocket.accept()
    session = get_live_auto_manager().get(session_id)
    if session is None:
        await websocket.send_text(json.dumps({"type": "error",
                                              "data": {"code": "NOT_FOUND", "message": "会话不存在"}}))
        await websocket.close()
        return
    queue = session.subscribe()
    snap = session.snapshot()
    try:
        await websocket.send_text(json.dumps({"type": "status", "data": snap["status"]}))
        await websocket.send_text(json.dumps({"type": "snapshot", "data": snap}))
        while True:
            # 空闲保活（spec D3）：终态由 `status` 事件承载，服务端不主动关连接（同 ws_paper）。
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_WS_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping"}))
                continue
            await websocket.send_text(json.dumps(event))
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        session.unsubscribe(queue)
