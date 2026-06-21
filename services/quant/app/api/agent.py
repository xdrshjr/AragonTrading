"""路由：Agent WS /ws/agent/{convId} + REST /agent/config、/agent/conversations/{id} (spec §4)。

WS 事件沿用 {type,data} 信封（同 _send_event）。run 在后台任务跑，使接收循环仍能处理 cancel；
断连即取消（P1-D）走与 cancel 同一中断路径，避免孤儿 run 继续烧配额。
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Query, Response, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.conversation import row_to_client_dict
from ..agent.manager import get_agent_manager
from ..config import get_settings
from ..db import repositories as repo
from ..db.session import get_session
from ..schemas import AgentConfigOut

router = APIRouter(tags=["agent"])
logger = logging.getLogger("aragon.agent.api")


@router.get("/agent/config", response_model=AgentConfigOut)
async def agent_config() -> dict:
    """是否已配置密钥 + 当前 provider / model（绝不返回 key，R3）。"""
    return get_agent_manager().config_info()


@router.get("/agent/conversations/{conversation_id}/messages")
async def agent_messages(conversation_id: str, limit: int = Query(200, ge=1, le=500),
                         session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = await repo.list_agent_messages(session, conversation_id, limit=limit)
    return [row_to_client_dict(r) for r in rows]


@router.delete("/agent/conversations/{conversation_id}", status_code=204)
async def agent_clear(conversation_id: str) -> Response:
    await get_agent_manager().clear(conversation_id)
    return Response(status_code=204)


@router.websocket("/ws/agent/{conversation_id}")
async def ws_agent(websocket: WebSocket, conversation_id: str) -> None:
    await websocket.accept()
    manager = get_agent_manager()
    await _send(websocket, "ready", {"conversationId": conversation_id, **manager.config_info()})
    history = await manager.history(conversation_id)
    await _send(websocket, "history", {"messages": history})

    async def emit(event: dict) -> None:
        await _send_raw(websocket, event)

    run_task: asyncio.Task | None = None
    try:
        while True:
            raw = await websocket.receive_text()
            run_task = await _dispatch(raw, conversation_id, manager, emit, run_task, websocket)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        # 断连即取消（P1-D）：停止 in-flight run（落盘已完成轮次），回收孤儿配额。
        await manager.cancel(conversation_id)
        await _drain(run_task, get_settings().agent_drain_timeout_seconds)


async def _dispatch(raw: str, conversation_id: str, manager, emit, run_task, websocket):
    """处理一条 client 消息，返回（可能新建的）run_task。"""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return run_task
    mtype = msg.get("type")
    if mtype == "cancel":
        await manager.cancel(conversation_id)
        return run_task
    if mtype == "user_message":
        if run_task is not None and not run_task.done():
            await _send(websocket, "error", {"code": "BUSY", "message": "上一条仍在处理，请稍候。"})
            return run_task
        data = msg.get("data") or {}
        return asyncio.create_task(
            manager.handle(conversation_id, data.get("text", ""), data.get("ui"), emit))
    return run_task


async def _drain(run_task: asyncio.Task | None, timeout: float) -> None:
    """有界等待 in-flight run 收束（H4）：超时即 cancel 孤儿并 warning，清理路径永不挂死。"""
    if run_task is None or run_task.done():
        return
    try:
        await asyncio.wait_for(run_task, timeout=timeout)
    except asyncio.TimeoutError:
        run_task.cancel()
        logger.warning("agent run task drained by timeout (%.1fs); possible orphan cancelled",
                       timeout)
    except Exception:  # noqa: BLE001 —— 取消路径的兜底，吞掉后台任务残余异常
        logger.debug("agent run task ended with error during drain", exc_info=True)


async def _send(websocket: WebSocket, event_type: str, data: dict) -> None:
    await _send_raw(websocket, {"type": event_type, "data": data})


async def _send_raw(websocket: WebSocket, event: dict) -> None:
    try:
        await websocket.send_text(json.dumps(event, ensure_ascii=False))
    except Exception:  # noqa: BLE001 —— 连接已关闭：吞掉发送异常，不打断取消路径
        pass
