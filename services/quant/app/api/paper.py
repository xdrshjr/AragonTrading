"""路由：实时模拟 start/stop/sessions + ws /ws/paper/{sid} (spec §9.1 / §9.2)。"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import repositories as repo
from ..db.session import get_session
from ..paper.manager import PaperSessionLimitError, get_paper_manager
from ..schemas import PaperStartReq
from ..strategies.base import get_strategy_class

router = APIRouter(tags=["paper"])


@router.post("/paper/start")
async def paper_start(body: PaperStartReq,
                      session: AsyncSession = Depends(get_session)) -> dict:
    row = await repo.get_strategy_row(session, body.strategyId)
    if row is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "策略不存在"})
    cls = get_strategy_class(row.template_key)
    if cls is None:
        raise HTTPException(400, detail={"code": "UNAVAILABLE", "message": "策略模板已失效"})
    try:
        params = {**cls.meta.default_params, **json.loads(row.params_json or "{}")}
    except json.JSONDecodeError:
        params = dict(cls.meta.default_params)
    strategy = cls(params)

    settings = get_settings()
    initial_capital = body.initialCapital or settings.initial_capital
    try:
        sess = await get_paper_manager().start(
            strategy, body.strategyId, body.instId, body.bar, initial_capital,
            source=body.source, speed=body.speed, replay_bars=body.replayBars,
            priming=body.priming, priming_bars=body.primingBars)
    except PaperSessionLimitError as exc:
        raise HTTPException(429, detail={"code": "SESSION_LIMIT", "message": str(exc)})
    return {"sessionId": sess.session_id, "runId": sess.run_id}


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
            event = await queue.get()
            await websocket.send_text(json.dumps(event))
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        session.unsubscribe(queue)
