"""路由：权重模型 CRUD + 训练记录读 / 删 + 流式训练启动 / 取消 + WS 实时流（weight-model-workspace §五）。

第 12 个路由：字段 camelCase、时间秒级 UTC、错误体统一 ``{error:{code,message}}``（沿用 main.py 异常处理器）。
``get_rest_client`` 在本命名空间解析，故测试对 ``app.api.weight_models.get_rest_client`` 的 monkeypatch 生效
（注入给流式作业）。WS 端点抄 ``api/paper.py:ws_paper``：订阅即全量回放缓冲（含终态，绝不悬挂，R-06）。
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_session
from ..factors import weight_model_service as service
from ..factors.weight_train_manager import get_weight_train_manager
from ..okx.rest_client import get_rest_client
from ..schemas import WeightModelCreateReq, WeightModelUpdateReq, WeightTrainStartReq

router = APIRouter(tags=["weight-models"])

_TERMINAL_TYPES = ("done", "error", "cancelled")
_NOT_FOUND = {"code": "NOT_FOUND", "message": "权重模型不存在"}
_WS_HEARTBEAT_SECONDS = 20.0   # H-B7：静默此秒数后下发 ping（半开探测 + 满队列丢终态兜底）


# ============================ 模型 CRUD（§5.1） ============================
@router.get("/weight-models")
async def list_weight_models(enabled: bool | None = Query(None),
                             session: AsyncSession = Depends(get_session)) -> list[dict]:
    return await service.list_models(session, enabled=enabled)


@router.post("/weight-models", status_code=201)
async def create_weight_model(body: WeightModelCreateReq,
                              session: AsyncSession = Depends(get_session)) -> dict:
    return await service.create_model(session, body)


# 训练启动 / 取消先于 /{model_id} 声明，避免路径参数捕获 "train"（取消多一段，天然不冲突）。
@router.post("/weight-models/train")
async def start_weight_train(body: WeightTrainStartReq,
                             session: AsyncSession = Depends(get_session)) -> dict:
    return await service.start_training(session, get_rest_client(), body)


@router.delete("/weight-models/train/{job_id}")
async def cancel_weight_train(job_id: str) -> Response:
    """协作式取消训练作业（R-03）：running → 202 cancelling；已终态 → 200 幂等；未知 → 404，绝不抛。"""
    result = service.cancel_training(job_id)["result"]
    if result == "unknown":
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "训练作业不存在或已过期"})
    status_code = 202 if result == "cancelling" else 200
    return Response(content=json.dumps({"jobId": job_id, "status": result}),
                    media_type="application/json", status_code=status_code)


@router.get("/weight-models/{model_id}")
async def get_weight_model(model_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    row = await service.get_model(session, model_id)
    if row is None:
        raise HTTPException(404, detail=_NOT_FOUND)
    return row


@router.put("/weight-models/{model_id}")
async def update_weight_model(model_id: int, body: WeightModelUpdateReq,
                              session: AsyncSession = Depends(get_session)) -> dict:
    row = await service.update_model(session, model_id, body)
    if row is None:
        raise HTTPException(404, detail=_NOT_FOUND)
    return row


@router.delete("/weight-models/{model_id}", status_code=204)
async def delete_weight_model(model_id: int,
                              session: AsyncSession = Depends(get_session)) -> Response:
    if await service.delete_model(session, model_id) == "not_found":
        raise HTTPException(404, detail=_NOT_FOUND)
    return Response(status_code=204)


# ============================ 训练记录（§5.2） ============================
@router.get("/weight-models/{model_id}/runs")
async def list_weight_runs(model_id: int, limit: int | None = Query(None, ge=1, le=500),
                           status: str | None = Query(None),
                           session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = await service.list_runs(session, model_id, limit=limit, status=status)
    if rows is None:
        raise HTTPException(404, detail=_NOT_FOUND)
    return rows


@router.get("/weight-models/{model_id}/runs/{run_id}")
async def get_weight_run(model_id: int, run_id: int,
                         session: AsyncSession = Depends(get_session)) -> dict:
    row = await service.get_run(session, model_id, run_id)
    if row is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "训练记录不存在"})
    return row


@router.delete("/weight-models/{model_id}/runs/{run_id}", status_code=204)
async def delete_weight_run(model_id: int, run_id: int,
                            session: AsyncSession = Depends(get_session)) -> Response:
    result = await service.delete_run(session, model_id, run_id)
    if result == "not_found":
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "训练记录不存在"})
    if result == "running":
        raise HTTPException(409, detail={"code": "WEIGHT_RUN_RUNNING", "message": "训练进行中，无法删除"})
    return Response(status_code=204)


# ============================ 训练实时流（§5.4，抄 ws_paper） ============================
@router.websocket("/ws/weight-train/{job_id}")
async def ws_weight_train(websocket: WebSocket, job_id: str) -> None:
    await websocket.accept()
    job = get_weight_train_manager().get(job_id)
    if job is None:
        await websocket.send_text(json.dumps({
            "type": "error", "data": {"code": "NOT_FOUND", "message": "训练作业不存在或已过期"}}))
        await websocket.close()
        return
    # subscribe() 与 snapshot() 间无 await：协作式单线程下原子，snapshot 为缓冲前缀、queue 为其后增量，不重复（抄 ws_paper）。
    queue = job.subscribe()
    snapshot = job.snapshot()
    try:
        for event in snapshot:
            await websocket.send_text(json.dumps(event))
        # 已终态（快作业先于连线结束，R-06）：回放完终态后立即关闭，绝不悬挂。
        if snapshot and snapshot[-1].get("type") in _TERMINAL_TYPES:
            await websocket.close()
            return
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_WS_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                # H-B7：静默超时 → 下发 ping 兼做半开探测；并兜底检查终态（满队列若丢终态也不悬挂）。
                await websocket.send_text(json.dumps({"type": "ping"}))
                if job.is_terminal:
                    buf = job.snapshot()
                    if buf and buf[-1].get("type") in _TERMINAL_TYPES:
                        await websocket.send_text(json.dumps(buf[-1]))
                    await websocket.close()
                    break
                continue
            await websocket.send_text(json.dumps(event))
            if event.get("type") in _TERMINAL_TYPES:
                await websocket.close()
                break
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        job.unsubscribe(queue)
