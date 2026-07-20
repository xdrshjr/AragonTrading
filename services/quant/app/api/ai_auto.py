"""路由：AI 自动交易 start/stop/status + ws /ws/ai-trade/{sid}（ai-auto-trading §4）。

``start`` 受 ``AI_TRADE_ENABLED`` **与** ``LIVE_TRADING_ENABLED`` 双闸守门（自动路径直接调服务层，
须在此显式同时校验，任一 false → 403 ``AI_TRADE_DISABLED``，先于一切凭证读取）。``stop`` 刻意不守门：
即便运维中途关闭总闸，也须保证既有会话随时可停止并平掉自管持仓（安全优先）。``GET /status`` 只读不守门。
错误体统一 ``{error:{code,message}}``（经 main 全局 HTTPException 处理器包装）。
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_auto import get_ai_trade_manager
from ..ai_auto.backtest_manager import (
    AiBacktestError,
    AiBacktestStartRequest,
    get_ai_backtest_manager,
)
from ..ai_auto.base_rules import MaBandRule, resolve_ma_band_rule
from ..ai_auto.manager import (
    AiTradeGroupMemberInput,
    AiTradeGroupPreflightError,
    AiTradeGroupStartRequest,
    AiTradePreflightError,
    AiTradeSessionLimitError,
)
from ..config import Settings, get_settings
from ..db import repositories as repo
from ..db.session import get_session
from ..paper.strategy_factory import build_paper_strategy_spec
from ..schemas import (
    AiBacktestStartIn,
    AiTradeBaseRulesIn,
    AiTradeGroupStartIn,
    AiTradeHistoryDetailOut,
    AiTradeHistoryListOut,
    trade_out,
)

router = APIRouter(tags=["ai-trade"])

_WS_HEARTBEAT_SECONDS = 20.0   # 静默此秒数后下发 ping（半开探测 + 满队列丢终态兜底，镜像 weight-train）
_BACKTEST_TERMINAL_TYPES = ("done", "cancelled", "error")


def _require_ai_enabled(settings: Settings) -> None:
    """写类守门：AI 自动化总闸与底层写链路总闸须同时为 True，否则 403（先于凭证读取）。"""
    if not (settings.ai_trade_enabled and settings.live_trading_enabled):
        raise HTTPException(status_code=403, detail={
            "code": "AI_TRADE_DISABLED",
            "message": "AI 自动交易未启用（需 AI_TRADE_ENABLED 与 LIVE_TRADING_ENABLED 均开启）"})


def _resolve_position_step(raw: float | None, settings: Settings) -> float:
    """仓位操作档位步长解析（[R-2] **仅**缺省回填）：`None → settings 默认`，否则原样返回。

    不复夹 `[0,1]`——边界已由 schema `Field(ge=0, le=1)` 在入口拒绝（越界 422，与既有 `leverage:
    Field(gt=0, le=125)` 只在 schema 兜边界、API 不复夹的惯例一致）。`0` 为显式连续退出口，被接受。
    """
    return settings.ai_trade_default_position_step_pct if raw is None else raw


def _resolve_wake_interval_s(minutes: float | None, settings: Settings) -> float:
    """Agent 墙钟定时唤醒周期解析（分钟 → 秒并**夹紧**，与 `_resolve_position_step` 同风格但夹紧而非仅回填）。

    `None → 默认（ai_trade_wake_interval_default_min）`；`<=0 → 0`（关闭定时唤醒，退出口）；否则夹到
    `[ai_trade_wake_interval_floor_s, ai_trade_wake_interval_ceil_min*60]` 秒（越界不拒绝，向 UX 友好收敛；
    前端 validateWakeInterval 另做即时报错，二者区间一致、职责互补，RN-9）。
    """
    if minutes is None:
        minutes = settings.ai_trade_wake_interval_default_min
    if minutes <= 0:
        return 0.0
    lo = settings.ai_trade_wake_interval_floor_s
    hi = settings.ai_trade_wake_interval_ceil_min * 60.0
    return min(max(minutes * 60.0, lo), hi)


def _resolve_base_rules(raw: AiTradeBaseRulesIn | None,
                        settings: Settings) -> MaBandRule | None:
    """【基础策略】默认开启语义的**唯一解析点**（ai-trade-base-strategy-ma-band §2.10/P1-5）。

    字段缺省 ⇒ 按 settings 装配开启态（老客户端 / 桌面旧包不发该字段也默认开启，符合需求「默认开启」）；
    显式 `enabled=false` ⇒ `None`（该会话不装配）；运维总闸关 ⇒ 恒 `None`。

    **刻意不在 dataclass 层给开启默认值**：`AiTradeGroupStartRequest.base_rule` / `AiBacktestSpec`
    的字段默认仍是 `None`，故既有直接构造它们的单测行为逐字不变。实盘与回测共用本函数，保证「用回测
    调出的参数在实盘不失真」（P1-4）。
    """
    raw_rule = raw.maBandEntry if raw is not None else None
    return resolve_ma_band_rule(
        raw_rule.model_dump() if raw_rule is not None else None, settings)


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
async def ai_trade_start(body: AiTradeGroupStartIn, session: AsyncSession = Depends(get_session),
                         settings: Settings = Depends(get_settings)) -> dict:
    """启动 AI 自动交易「分组」（受双闸守门；§4.1 两段式前置门禁 + 整组回滚；回执含逐成员夹紧后预算）。

    预算总额天花板由服务端解析 `cap_frac = min(maxBudgetPct/100, ai_trade_max_capital_frac(默认 1.0=不约束),
    live_auto_group_max_capital_frac)`，与非 AI 多标的分组口径统一（ai-trade-total-budget-allocation 有意放开
    历史 5% 绝对红线）：该式恒等于前端预览所读 `effectiveMaxBudgetPct/100`，故「弹窗预览 == 落地」逐配置严格
    相等。留空默认 `default_frac = ai_trade_default_capital_frac`（1%，保守）；前端任何「上限」入参一概不接受。
    成员总预算按 allocationPct 切分（非倍增）。
    """
    _require_ai_enabled(settings)
    account_row = await _resolve_account(session, body.accountId)
    specs = await _build_specs(session, body.strategyIds)
    at = await repo.get_auto_trade_settings(session)
    # 天花板委托给账户级 maxBudgetPct 与运营天花板（与 effectiveMaxBudgetPct 同源，min 只收紧不放大）；
    # ai_trade_max_capital_frac 默认 1.0=不额外约束，运维可下调为 AI 专属更紧的绝对上限。
    cap_frac = min(at["maxBudgetPct"] / 100.0,
                   settings.ai_trade_max_capital_frac,          # AI 专属可选绝对天花板（默认 1.0=不约束）
                   settings.live_auto_group_max_capital_frac)   # 分组运营天花板（与 effectiveMaxBudgetPct 同源）
    req = AiTradeGroupStartRequest(
        specs=specs,
        members_in=[AiTradeGroupMemberInput(inst_id=m.instId, allocation_pct=m.allocationPct)
                    for m in body.members],
        bar=body.bar, account_row=account_row, settings=settings,
        context_refresh_rounds=body.contextRefreshRounds, allocation_mode=body.allocationMode,
        total_capital_usd=body.capitalUsd, leverage=body.leverage,
        take_profit_usd=body.takeProfitUsd, stop_loss_usd=body.stopLossUsd,
        stop_loss_pct=body.stopLossPct, take_profit_pct=body.takeProfitPct,  # E4：单仓 OCO 入参
        priming=body.priming, priming_bars=body.primingBars,
        budget_cap_frac=cap_frac, default_budget_frac=settings.ai_trade_default_capital_frac,
        position_step_pct=_resolve_position_step(body.positionStepPct, settings),
        wake_interval_s=_resolve_wake_interval_s(body.wakeIntervalMinutes, settings),
        base_rule=_resolve_base_rules(body.baseRules, settings))
    try:
        await get_ai_trade_manager().start_group(req)
    except AiTradeGroupPreflightError as exc:
        # 整组前置失败须透传逐标的 members 明细（全局处理器据此包成 {error:{code,message,members}}）。
        raise HTTPException(exc.status, detail={
            "code": exc.code, "message": exc.message, "members": exc.members})
    except (AiTradeSessionLimitError, AiTradePreflightError) as exc:
        raise HTTPException(exc.status, detail={"code": exc.code, "message": exc.message})
    return get_ai_trade_manager().group_status()


@router.post("/ai-trade/{group_id}/stop")
async def ai_trade_stop(group_id: str) -> dict:
    """停整组（停所有成员、各自平自管仓、落 Run 终态；刻意不守门，确保随时可停止平仓）。"""
    result = await get_ai_trade_manager().stop_group(group_id)
    if result is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "分组不存在"})
    return result


@router.post("/ai-trade/{group_id}/member/{inst_id}/stop")
async def ai_trade_member_stop(group_id: str, inst_id: str) -> dict:
    """仅停某成员（其余续跑；不守门）；停后无运行成员则分组清空。"""
    result = await get_ai_trade_manager().stop_member(group_id, inst_id)
    if result is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "分组或成员不存在"})
    return result


@router.get("/ai-trade/status")
async def ai_trade_status(settings: Settings = Depends(get_settings)) -> dict:
    """当前活跃 AI 分组（无则 group:null）；前端重载后据此重连各成员 WS（只读，不守门）。"""
    return {"group": get_ai_trade_manager().group_status(),
            "aiTradeEnabled": settings.ai_trade_enabled}


@router.get("/ai-trade/history", response_model=AiTradeHistoryListOut)
async def ai_trade_history(limit: int = Query(20, ge=1, le=100),
                          offset: int = Query(0, ge=0),
                          session: AsyncSession = Depends(get_session)) -> dict:
    """历史 AI 自动交易会话列表（ai-trade-stats-dashboard §3.7）：按 group 聚合、startedTs DESC、
    分页；排除当前运行中分组（其在「当前」视图展示）。只读，不守门（历史查看不涉及下单）。"""
    active = get_ai_trade_manager().group_status()
    exclude = active["groupId"] if active else None
    return await repo.list_ai_trade_groups(
        session, limit=limit, offset=offset, exclude_group_id=exclude)


@router.get("/ai-trade/history/{group_id}", response_model=AiTradeHistoryDetailOut)
async def ai_trade_history_detail(group_id: str,
                                 session: AsyncSession = Depends(get_session)) -> dict:
    """单次 AI 自动交易历史详情（§3.7）：会话头 + 逐成员成交（trade_out）/ metrics + 后端聚合 KPI；组不存在 → 404。"""
    detail = await repo.get_ai_trade_group_detail(session, group_id)
    if detail is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "历史会话不存在"})
    for member in detail["members"]:
        member["trades"] = [trade_out(t) for t in member["trades"]]
    return detail


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
        min_consensus=body.minConsensus,
        position_step_pct=_resolve_position_step(body.positionStepPct, settings),
        base_rule=_resolve_base_rules(body.baseRules, settings))
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
