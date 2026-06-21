"""路由：Agent WS /ws/agent/{convId} + REST /agent/config、/agent/conversations/{id} (spec §4)。

WS 事件沿用 {type,data} 信封（同 _send_event）。韧性流式（iteration 3, RS-4）：连接即 `attach`、
断连即 `detach`（脱离而非取消）、收 `resume` 即重放续流；BUSY 改会话级 `sess.running`（跨重连可见）。
run 归属会话、跨连接存活；宽限耗尽才由 manager 取消并 drain。
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Query, Response, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.conversation import row_to_client_dict
from ..agent.manager import AgentManager, get_agent_manager
from ..db import repositories as repo
from ..db.session import get_session
from ..schemas import AgentConfigOut

router = APIRouter(tags=["agent"])


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

    # 连接即 attach（注册 sink；run 归属会话，RS-4）：返回会话供 BUSY 快路与续流判定。
    sess = await manager.attach(conversation_id, emit)
    try:
        while True:
            raw = await websocket.receive_text()
            await _dispatch(raw, conversation_id, manager, sess, emit, websocket)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        # 断连即脱离（非取消，RS-4）：run 进入宽限窗口，宽限耗尽才由 manager 取消并 drain。
        await manager.detach(conversation_id, emit)


async def _dispatch(raw: str, conversation_id: str, manager, sess, emit, websocket) -> None:
    """处理一条 client 消息（user_message / cancel / resume）。"""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return
    mtype = msg.get("type")
    if mtype == "cancel":
        await manager.cancel(conversation_id)
    elif mtype == "resume":
        data = msg.get("data") or {}
        await manager.resume(conversation_id, _as_int(data.get("lastSeq"), 0), emit)
    elif mtype == "user_message":
        await _handle_user_message(msg, conversation_id, manager, sess, websocket)


async def _handle_user_message(msg, conversation_id: str, manager, sess, websocket) -> None:
    """user_message：会话级 BUSY 快路（RN-4：权威是 run() 内层守卫，此处仅 best-effort）。"""
    if sess.running:
        await _send(websocket, "error", {"code": "BUSY", "message": "上一条仍在处理，请稍候。"})
        return
    data = msg.get("data") or {}
    await manager.handle(conversation_id, data.get("text", ""), data.get("ui"))


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def _drain(run_task: asyncio.Task | None, timeout: float) -> None:
    """有界等待 in-flight run 收束（H4，宽限取消路径兜底）：委托 AgentManager.drain（单一实现）。"""
    await AgentManager.drain(run_task, timeout)


async def _send(websocket: WebSocket, event_type: str, data: dict) -> None:
    await _send_raw(websocket, {"type": event_type, "data": data})


async def _send_raw(websocket: WebSocket, event: dict) -> None:
    try:
        await websocket.send_text(json.dumps(event, ensure_ascii=False))
    except Exception:  # noqa: BLE001 —— 连接已关闭：吞掉发送异常，不打断取消路径
        pass
