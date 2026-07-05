"""路由：AI 自动交易 start/stop/status + ws /ws/ai-trade/{sid}（ai-auto-trading §4）。

``start`` 受 ``AI_TRADE_ENABLED`` **与** ``LIVE_TRADING_ENABLED`` 双闸守门（自动路径直接调服务层，
须在此显式同时校验，任一 false → 403 ``AI_TRADE_DISABLED``，先于一切凭证读取）。``stop`` 刻意不守门：
即便运维中途关闭总闸，也须保证既有会话随时可停止并平掉自管持仓（安全优先）。``GET /status`` 只读不守门。
错误体统一 ``{error:{code,message}}``（经 main 全局 HTTPException 处理器包装）。
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_auto import get_ai_trade_manager
from ..ai_auto.backtest_manager import (
    AiBacktestError,
    AiBacktestStartRequest,
    get_ai_backtest_manager,
)
from ..ai_auto.manager import (
    AiTradePreflightError,
    AiTradeSessionLimitError,
    AiTradeStartRequest,
)
from ..config import Settings, get_settings
from ..db import repositories as repo
from ..db.session import get_session
from ..paper.strategy_factory import build_paper_strategy_spec
from ..schemas import AiBacktestStartIn, AiTradeStartIn, run_out

router = APIRouter(tags=["ai-trade"])

_WS_HEARTBEAT_SECONDS = 20.0   # 静默此秒数后下发 ping（半开探测 + 满队列丢终态兜底，镜像 weight-train）
_BACKTEST_TERMINAL_TYPES = ("done", "cancelled", "error")


def _require_ai_enabled(settings: Settings) -> None:
    """写类守门：AI 自动化总闸与底层写链路总闸须同时为 True，否则 403（先于凭证读取）。"""
    if not (settings.ai_trade_enabled and settings.live_trading_enabled):
        raise HTTPException(status_code=403, detail={
            "code": "AI_TRADE_DISABLED",
            "message": "AI 自动交易未启用（需 AI_TRADE_ENABLED 与 LIVE_TRADING_ENABLED 均开启）"})


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


async def _build_specs(session: AsyncSession, strategy_ids: list[int]) -> list:
    """逐个策略行取存在性 + `build_paper_strategy_spec` 可实例化；任一失败 → AI_TRADE_STRATEGIES_INVALID。"""
    specs = []
    for sid in strategy_ids:
        row = await repo.get_strategy_row(session, sid)
        if row is None:
            raise HTTPException(400, detail={
                "code": "AI_TRADE_STRATEGIES_INVALID", "message": f"策略不存在：{sid}"})
        try:
            specs.append(build_paper_strategy_spec(row))
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            raise HTTPException(400, detail={
                "code": "AI_TRADE_STRATEGIES_INVALID",
                "message": detail.get("message", f"策略无法实例化：{sid}")}) from exc
    return specs


@router.post("/ai-trade/start")
async def ai_trade_start(body: AiTradeStartIn, session: AsyncSession = Depends(get_session),
                         settings: Settings = Depends(get_settings)) -> dict:
    """启动 AI 自动交易（受双闸守门；§2.5 前置门禁；回执 budgetUsd 为夹紧后最终生效预算）。"""
    _require_ai_enabled(settings)
    account_row = await _resolve_account(session, body.accountId)
    specs = await _build_specs(session, body.strategyIds)
    req = AiTradeStartRequest(
        specs=specs, inst_id=body.instId, bar=body.bar, account_row=account_row,
        settings=settings, context_refresh_rounds=body.contextRefreshRounds,
        capital_usd=body.capitalUsd, leverage=body.leverage,
        take_profit_usd=body.takeProfitUsd, stop_loss_usd=body.stopLossUsd,
        stop_loss_pct=body.stopLossPct, take_profit_pct=body.takeProfitPct,  # E4：单仓 OCO 入参
        priming=body.priming, priming_bars=body.primingBars)
    try:
        sess = await get_ai_trade_manager().start(req)
    except (AiTradeSessionLimitError, AiTradePreflightError) as exc:
        raise HTTPException(exc.status, detail={"code": exc.code, "message": exc.message})
    risk = sess.executor.risk       # E4：回执生效 pct（服务端夹紧后的实际值，供前端确认夹紧结果）
    return {"sessionId": sess.session_id, "runId": sess.run_id, "instId": body.instId,
            "bar": body.bar, "strategyIds": sess.strategy_ids,
            "strategyNames": sess.strategy_names,
            "budgetUsd": sess.executor.budget_usd, "leverage": sess.executor.leverage,
            "contextRefreshRounds": sess.context_refresh_rounds,
            "stopLossPct": risk.stop_loss_pct, "takeProfitPct": risk.take_profit_pct}


@router.post("/ai-trade/{session_id}/stop")
async def ai_trade_stop(session_id: str,
                        session: AsyncSession = Depends(get_session)) -> dict:
    """平掉自管持仓 + 停会话 + 落 Run 终态（刻意不守门，确保任何时候可停止平仓）。"""
    result = await get_ai_trade_manager().stop(session_id)
    if result is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "会话不存在"})
    sess, flattened = result
    run = await repo.get_run(session, sess.run_id)
    return {"sessionId": session_id, "stopReason": sess.stop_reason,
            "run": run_out(run) if run is not None else None, "flattened": flattened}


@router.get("/ai-trade/status")
async def ai_trade_status(settings: Settings = Depends(get_settings)) -> dict:
    """当前活跃 AI 会话（无则 session:null）；前端重载后据此重连 WS（只读，不守门）。"""
    return {"session": get_ai_trade_manager().status(),
            "aiTradeEnabled": settings.ai_trade_enabled}


@router.websocket("/ws/ai-trade/{session_id}")
async def ws_ai_trade(websocket: WebSocket, session_id: str) -> None:
    """事件协议与 /ws/live-auto/{id} 同信封；连接即原子 subscribe→status→snapshot（防丢事件）。"""
    await websocket.accept()
    session = get_ai_trade_manager().get(session_id)
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
            event = await queue.get()
            await websocket.send_text(json.dumps(event))
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        session.unsubscribe(queue)


# ============================ AI 回测模式（ai-auto-trading-backtest §4）============================
# 回测子路由**仅**校验 Agent 已配置（在 AiBacktestManager.start 内经 resolve_active_provider），
# **不**校验 AI_TRADE_ENABLED/LIVE_TRADING_ENABLED 双闸——回测走纯模拟 SimBroker、不下真单、无真实资金面。
@router.post("/ai-trade/backtest/start")
async def ai_backtest_start(body: AiBacktestStartIn, session: AsyncSession = Depends(get_session),
                            settings: Settings = Depends(get_settings)) -> dict:
    """启动一个 AI 自动交易回测作业（§2.9 前置门禁；回执含 plannedRounds 与夹紧后生效资金/杠杆）。"""
    specs = await _build_specs(session, body.strategyIds)
    req = AiBacktestStartRequest(
        specs=specs, inst_id=body.instId, bar=body.bar, start_ts=body.startTs, end_ts=body.endTs,
        settings=settings, context_refresh_rounds=body.contextRefreshRounds,
        decision_every_bars=body.decisionEveryBars, initial_capital=body.initialCapital,
        leverage=body.leverage, stop_loss_pct=body.stopLossPct, take_profit_pct=body.takeProfitPct,
        take_profit_usd=body.takeProfitUsd, stop_loss_usd=body.stopLossUsd,
        min_consensus=body.minConsensus)
    try:
        job = await get_ai_backtest_manager().start(req)
    except AiBacktestError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code, "message": exc.message})
    runner = job.runner
    return {"jobId": job.job_id, "instId": runner.inst_id, "bar": runner.bar,
            "strategyIds": runner.strategy_ids, "startTs": runner.start_ts, "endTs": runner.end_ts,
            "decisionEveryBars": runner.decision_every_bars, "plannedRounds": runner.planned_rounds,
            "initialCapital": runner.initial_capital, "leverage": runner.leverage}


@router.post("/ai-trade/backtest/{job_id}/cancel")
async def ai_backtest_cancel(job_id: str) -> dict:
    """协作式取消回测作业（不存在 → 404）；实际终态经 WS `cancelled` 事件驱动前端。"""
    outcome = get_ai_backtest_manager().cancel(job_id)
    if outcome == "unknown":
        raise HTTPException(404, detail={"code": "AI_BACKTEST_JOB_NOT_FOUND",
                                         "message": "无此回测任务"})
    job = get_ai_backtest_manager().get(job_id)
    return {"jobId": job_id, "state": job.state if job is not None else "cancelled"}


@router.get("/ai-trade/backtest/{job_id}")
async def ai_backtest_get(job_id: str) -> dict:
    """回测作业只读状态（前端重载后据此重发现 + 重连 WS）。"""
    job = get_ai_backtest_manager().get(job_id)
    if job is None:
        raise HTTPException(404, detail={"code": "AI_BACKTEST_JOB_NOT_FOUND",
                                         "message": "无此回测任务"})
    return {"job": job.status_dict()}


@router.websocket("/ws/ai-backtest/{job_id}")
async def ws_ai_backtest(websocket: WebSocket, job_id: str) -> None:
    """回测事件流（同信封 {type,data}，镜像 weight-train）：连接即原子 subscribe→snapshot 回放→续订新事件。"""
    await websocket.accept()
    job = get_ai_backtest_manager().get(job_id)
    if job is None:
        await websocket.send_text(json.dumps({
            "type": "error", "data": {"code": "AI_BACKTEST_JOB_NOT_FOUND",
                                      "message": "无此回测任务或已过期"}}))
        await websocket.close()
        return
    queue = job.subscribe()                       # subscribe() 与 snapshot_events() 间无 await：单线程下原子
    snapshot = job.snapshot_events()
    try:
        for event in snapshot:
            await websocket.send_text(json.dumps(event))
        if snapshot and snapshot[-1].get("type") in _BACKTEST_TERMINAL_TYPES:
            await websocket.close()               # 已终态（快作业先于连线结束）：回放完即关，绝不悬挂
            return
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_WS_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping"}))
                if job.is_terminal:               # 满队列若丢终态：关闭前补发 result + 终态事件
                    tail = job.snapshot_events()
                    last_result = next((e for e in reversed(tail) if e.get("type") == "result"), None)
                    if last_result is not None:
                        await websocket.send_text(json.dumps(last_result))
                    if tail and tail[-1].get("type") in _BACKTEST_TERMINAL_TYPES:
                        await websocket.send_text(json.dumps(tail[-1]))
                    await websocket.close()
                    break
                continue
            await websocket.send_text(json.dumps(event))
            if event.get("type") in _BACKTEST_TERMINAL_TYPES:
                await websocket.close()
                break
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        job.unsubscribe(queue)
