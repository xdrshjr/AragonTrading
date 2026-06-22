"""路由：Agent WS /ws/agent/{convId} + REST /agent/config、/agent/conversations/{id} (spec §4)。

WS 事件沿用 {type,data} 信封（同 _send_event）。韧性流式（iteration 3, RS-4）：连接即 `attach`、
断连即 `detach`（脱离而非取消）、收 `resume` 即重放续流；BUSY 改会话级 `sess.running`（跨重连可见）。
run 归属会话、跨连接存活；宽限耗尽才由 manager 取消并 drain。
"""
from __future__ import annotations

import asyncio
import json

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.conversation import row_to_client_dict
from ..agent.manager import AgentManager, get_agent_manager
from ..agent.providers import config_from_row, probe_provider
from ..config import get_settings
from ..db import repositories as repo
from ..db.models import AgentProvider
from ..db.session import get_session
from ..schemas import (
    AgentConfigOut,
    AgentProviderIn,
    AgentProviderUpdate,
    ProviderTestReq,
    ProviderTestResult,
    ProvidersListOut,
    provider_out,
)

router = APIRouter(tags=["agent"])


@router.get("/agent/config", response_model=AgentConfigOut)
async def agent_config() -> dict:
    """是否已配置密钥 + 当前生效服务 provider / model / providerId / label（绝不返回 key，R3）。"""
    return await get_agent_manager().active_config()


@router.get("/agent/conversations/{conversation_id}/messages")
async def agent_messages(conversation_id: str, limit: int = Query(200, ge=1, le=500),
                         session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = await repo.list_agent_messages(session, conversation_id, limit=limit)
    return [row_to_client_dict(r) for r in rows]


@router.delete("/agent/conversations/{conversation_id}", status_code=204)
async def agent_clear(conversation_id: str) -> Response:
    await get_agent_manager().clear(conversation_id)
    return Response(status_code=204)


# ============================ AI 模型服务商（设置中心 REST，spec §4.1） ============================
def _not_found() -> None:
    raise HTTPException(status_code=404,
                        detail={"code": "PROVIDER_NOT_FOUND", "message": "服务不存在"})


def _require_http_url(url: str) -> None:
    """边界一次性校验（C5）：APIURL 须 http(s):// 前缀，否则 400。"""
    if not url.strip().lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400,
                            detail={"code": "INVALID_BASE_URL",
                                    "message": "APIURL 需以 http:// 或 https:// 开头"})


@router.get("/agent/providers", response_model=ProvidersListOut)
async def list_providers(session: AsyncSession = Depends(get_session)) -> dict:
    """全量服务商（脱敏）+ 当前默认 id + 解析后的当前生效服务摘要。"""
    rows = await repo.list_agent_providers(session)
    default = next((r for r in rows if r.is_default), None)
    active = await get_agent_manager().active_config()
    return {"providers": [provider_out(r) for r in rows],
            "defaultId": default.id if default else None, "active": active}


@router.post("/agent/providers", status_code=201)
async def create_provider(body: AgentProviderIn,
                          session: AsyncSession = Depends(get_session)) -> dict:
    """新增自定义服务（kind=custom）。"""
    _require_http_url(body.baseUrl)
    values = repo.ProviderValues(
        label=body.label, api_standard=body.apiStandard, base_url=body.baseUrl,
        api_key=body.apiKey, model=body.model, user_agent=body.userAgent or "claude-code/1.0")
    row = await repo.create_agent_provider(session, kind="custom", values=values)
    return provider_out(row)


@router.put("/agent/providers/{provider_id}")
async def update_provider(provider_id: int, body: AgentProviderUpdate,
                          session: AsyncSession = Depends(get_session)) -> dict:
    """编辑服务（apiKey 留空=保留原 key；官方项锁 slug/kind/标准）。"""
    if body.baseUrl is not None:
        _require_http_url(body.baseUrl)
    patch = repo.ProviderPatch(
        label=body.label, api_standard=body.apiStandard, base_url=body.baseUrl,
        api_key=body.apiKey, model=body.model, user_agent=body.userAgent)
    row = await repo.update_agent_provider(session, provider_id, patch=patch)
    if row is None:
        _not_found()
    return provider_out(row)


@router.delete("/agent/providers/{provider_id}", status_code=204)
async def delete_provider(provider_id: int,
                          session: AsyncSession = Depends(get_session)) -> Response:
    """删除自定义服务；官方项 400；删默认后自动回落含 key 行（P2-Fallback）。"""
    outcome = await repo.delete_agent_provider(session, provider_id)
    if outcome == "not_found":
        _not_found()
    if outcome == "not_deletable":
        raise HTTPException(status_code=400,
                            detail={"code": "PROVIDER_NOT_DELETABLE", "message": "官方服务不可删除"})
    return Response(status_code=204)


@router.post("/agent/providers/{provider_id}/default", response_model=ProvidersListOut)
async def set_default_provider(provider_id: int,
                               session: AsyncSession = Depends(get_session)) -> dict:
    """设为默认（事务清其余 is_default）；返回最新列表。"""
    if not await repo.set_default_agent_provider(session, provider_id):
        _not_found()
    return await list_providers(session)


@router.post("/agent/providers/{provider_id}/test", response_model=ProviderTestResult)
async def test_provider(provider_id: int,
                        session: AsyncSession = Depends(get_session)) -> dict:
    """测存量服务连通性（用库内 key）。"""
    row = await repo.get_agent_provider(session, provider_id)
    if row is None:
        _not_found()
    return await probe_provider(config_from_row(row, get_settings()))


@router.post("/agent/providers/test", response_model=ProviderTestResult)
async def test_provider_draft(body: ProviderTestReq) -> dict:
    """测草稿连通性（首存前；须带显式 apiKey）。"""
    settings = get_settings()
    draft = AgentProvider(
        slug="draft", kind="custom", label="draft", api_standard=body.apiStandard,
        base_url=body.baseUrl, api_key=body.apiKey, model=body.model,
        user_agent=body.userAgent or settings.agent_user_agent)
    return await probe_provider(config_from_row(draft, settings))


@router.websocket("/ws/agent/{conversation_id}")
async def ws_agent(websocket: WebSocket, conversation_id: str) -> None:
    await websocket.accept()
    manager = get_agent_manager()
    await _send(websocket, "ready",
                {"conversationId": conversation_id, **(await manager.active_config())})
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
