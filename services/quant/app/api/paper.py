"""路由：实时模拟 start/stop/sessions + ws /ws/paper/{sid} (spec §9.1 / §9.2)。"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import repositories as repo
from ..db.session import get_session
from ..paper.manager import (
    PaperBatchStrategy,
    PaperSessionLimitError,
    get_paper_manager,
)
from ..paper.strategy_factory import build_paper_strategy_spec
from ..schemas import PaperBatchStartReq, PaperStartReq

router = APIRouter(tags=["paper"])

# 静默此秒数后下发 ping（半开探测，镜像 ws_weight_train / ws_ai_backtest）。会话流的静默是**正常态**，
# 而客户端 `lib/ws.ts` 的 45s 看门狗会把它误判成断连并每 46 秒重连一次——保活帧消除那条假断连回路。
_WS_HEARTBEAT_SECONDS = 20.0


@router.post("/paper/start")
async def paper_start(body: PaperStartReq,
                      session: AsyncSession = Depends(get_session)) -> dict:
    row = await repo.get_strategy_row(session, body.strategyId)
    if row is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "策略不存在"})
    strategy_spec = build_paper_strategy_spec(row)

    settings = get_settings()
    initial_capital = body.initialCapital or settings.initial_capital
    try:
        sess = await get_paper_manager().start(
            strategy_spec.strategy, strategy_spec.strategy_id, body.instId, body.bar, initial_capital,
            source=body.source, speed=body.speed, replay_bars=body.replayBars,
            priming=body.priming, priming_bars=body.primingBars,
            strategy_name=strategy_spec.strategy_name,
            strategy_kind=strategy_spec.strategy_kind,
            config_extra=strategy_spec.config_extra)
    except PaperSessionLimitError as exc:
        raise HTTPException(429, detail={"code": "SESSION_LIMIT", "message": str(exc)})
    return {
        "sessionId": sess.session_id,
        "runId": sess.run_id,
        "strategyKind": strategy_spec.strategy_kind,
    }


@router.post("/paper/batch/start")
async def paper_batch_start(body: PaperBatchStartReq,
                            session: AsyncSession = Depends(get_session)) -> dict:
    strategy_ids = dedupe_ids(body.strategyIds)
    settings = get_settings()
    if len(strategy_ids) > settings.max_paper_sessions:
        raise HTTPException(429, detail={
            "code": "SESSION_LIMIT",
            "message": f"requested sessions exceed limit {settings.max_paper_sessions}",
        })

    batch_items: list[PaperBatchStrategy] = []
    missing_ids: list[int] = []
    for strategy_id in strategy_ids:
        row = await repo.get_strategy_row(session, strategy_id)
        if row is None:
            missing_ids.append(strategy_id)
            continue
        strategy_spec = build_paper_strategy_spec(row)
        batch_items.append(PaperBatchStrategy(
            strategy_spec.strategy_id,
            strategy_spec.strategy_name,
            strategy_spec.strategy_kind,
            strategy_spec.strategy,
            strategy_spec.config_extra,
        ))
    if missing_ids:
        raise HTTPException(404, detail={
            "code": "NOT_FOUND",
            "message": f"strategy ids not found: {missing_ids}",
        })

    initial_capital = body.initialCapital or settings.initial_capital
    try:
        group = await get_paper_manager().start_batch(
            batch_items, body.instId, body.bar, initial_capital,
            source=body.source, speed=body.speed, replay_bars=body.replayBars,
            priming=body.priming, priming_bars=body.primingBars)
    except PaperSessionLimitError as exc:
        raise HTTPException(429, detail={"code": "SESSION_LIMIT", "message": str(exc)})
    except Exception as exc:
        raise HTTPException(500, detail={
            "code": "PAPER_BATCH_START_FAILED",
            "message": str(exc),
        })
    return {
        "groupId": group.group_id,
        "instId": group.inst_id,
        "bar": group.bar,
        "sessions": [group_session_out(item) for item in group.sessions],
    }


@router.post("/paper/batch/{group_id}/stop")
async def paper_batch_stop(group_id: str,
                           session: AsyncSession = Depends(get_session)) -> dict:
    group = await get_paper_manager().stop_batch(group_id)
    if group is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "group not found"})
    runs = []
    from ..schemas import run_out
    for item in group.sessions:
        run = await repo.get_run(session, item.run_id)
        if run is not None:
            runs.append({
                "strategyId": item.strategy_id,
                "sessionId": item.session_id,
                "run": run_out(run),
            })
    return {"groupId": group.group_id, "runs": runs}


@router.post("/paper/stop-all")
async def paper_stop_all(session: AsyncSession = Depends(get_session)) -> dict:
    """停止注册表中所有 running 会话，确定性地释放配额。

    接到前端「停止全部并重试」：刷新/重开渲染进程会让会话变成孤儿仍占满配额
    （进程级单例、WS 断开只取消订阅不停会话），用户据此一键回收。返回被停止的
    run 列表；无会话运行时为安全 no-op，不触碰已 stopped 的残留行。
    """
    stopped = await get_paper_manager().stop_all()
    from ..schemas import run_out
    runs = []
    for sess in stopped:
        run = await repo.get_run(session, sess.run_id)
        if run is not None:
            out = run_out(run)
            runs.append({
                "strategyId": out["strategyId"],
                "sessionId": sess.session_id,
                "run": out,
            })
    return {"stopped": len(stopped), "runs": runs}


@router.post("/paper/{session_id}/stop")
async def paper_stop(session_id: str,
                     session: AsyncSession = Depends(get_session)) -> dict:
    sess = await get_paper_manager().stop(session_id)
    if sess is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "会话不存在"})
    run = await repo.get_run(session, sess.run_id)
    from ..schemas import run_out
    return run_out(run) if run else {"sessionId": session_id, "state": "stopped"}


@router.get("/paper/sessions")
async def paper_sessions() -> list[dict]:
    return get_paper_manager().list_sessions()


@router.websocket("/ws/paper/{session_id}")
async def ws_paper(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    session = get_paper_manager().get(session_id)
    if session is None:
        await websocket.send_text(json.dumps({"type": "error",
                                              "data": {"code": "NOT_FOUND", "message": "会话不存在"}}))
        await websocket.close()
        return
    # subscribe() 与 snapshot() 间无 await：asyncio 协作式单线程下二者对事件循环原子，
    # 故 snapshot 之后才可能有新事件入队，前端「整体替换 + 其后追加」不会重复（R7）。
    queue = session.subscribe()
    snap = session.snapshot()
    try:
        # 立即同步当前状态，紧随其后下发快照（订阅晚于预热 / 断线重连均可恢复，B3）
        await websocket.send_text(json.dumps({"type": "status", "data": snap["status"]}))
        await websocket.send_text(json.dumps({"type": "snapshot", "data": snap}))
        while True:
            # 空闲保活（spec D3）：**不**照搬 job 流的「终态即补发并关闭」——会话流的终态语义由
            # `status` 事件承载，由前端决定何时收连接；服务端擅自关连接会打断「会话已停止但用户
            # 还在看成交明细」这个正常场景。
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


def dedupe_ids(strategy_ids: list[int]) -> list[int]:
    seen: set[int] = set()
    unique: list[int] = []
    for strategy_id in strategy_ids:
        if strategy_id in seen:
            continue
        seen.add(strategy_id)
        unique.append(strategy_id)
    return unique


def group_session_out(item) -> dict:
    return {
        "strategyId": item.strategy_id,
        "name": item.strategy_name,
        "strategyKind": item.strategy_kind,
        "sessionId": item.session_id,
        "runId": item.run_id,
        "state": item.state,
    }
