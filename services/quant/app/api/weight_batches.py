"""路由：多模型训练批次 CRUD + 择优 / 取消 / 删除 + WS 实时流（weight-multi-model-training §四）。

字段 camelCase、时间秒级 UTC、错误体统一 ``{error:{code,message}}``（沿用 main.py 异常处理器）。
``get_rest_client`` 在本命名空间解析，故测试对 ``app.api.weight_batches.get_rest_client`` 的 monkeypatch 生效
（注入给候选作业）。WS 端点抄 ``ws_weight_train``：订阅即全量回放缓冲（含终态，绝不悬挂，R-06）。
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Response, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_session
from ..factors import weight_batch_service as service
from ..factors import weight_explore_service as explore_service
from ..factors.weight_batch_manager import get_weight_batch_manager
from ..okx.rest_client import get_rest_client
from ..schemas import WeightBatchSelectReq, WeightBatchStartReq, WeightExploreStartReq
from .ws_guard import guard_ws_origin

router = APIRouter(tags=["weight-batches"])

_BATCH_TERMINAL = ("batchDone",)
_NOT_FOUND = {"code": "NOT_FOUND", "message": "训练批次不存在"}
_WS_HEARTBEAT_SECONDS = 20.0   # 静默此秒数后下发 ping（半开探测 + 满队列丢终态兜底，抄 ws_weight_train）


def _json_response(payload: dict, status_code: int) -> Response:
    return Response(content=json.dumps(payload), media_type="application/json", status_code=status_code)


# ============================ 批次 CRUD ============================
@router.post("/weight-models/batches", status_code=201)
async def start_weight_batch(body: WeightBatchStartReq,
                             session: AsyncSession = Depends(get_session)) -> dict:
    return await service.start_batch(session, get_rest_client(), body)


@router.post("/weight-models/explorations", status_code=201)
async def start_weight_exploration(body: WeightExploreStartReq,
                                   session: AsyncSession = Depends(get_session)) -> dict:
    """因子组合探索训练启动（factor-combo-exploration §4.1）：委托 ``weight_explore_service.start_exploration``。

    探索批次 = ``kind="explore"`` 批次，读 / 择优 / 取消 / 删除 / WS 全部复用上方既有批次端点。
    """
    return await explore_service.start_exploration(session, get_rest_client(), body)


@router.get("/weight-models/batches")
async def list_weight_batches(session: AsyncSession = Depends(get_session)) -> list[dict]:
    return await service.list_batches(session)


@router.get("/weight-models/batches/{batch_id}")
async def get_weight_batch(batch_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    row = await service.get_batch(session, batch_id)
    if row is None:
        raise HTTPException(404, detail=_NOT_FOUND)
    return row


@router.post("/weight-models/batches/{batch_id}/select")
async def select_weight_batch_run(batch_id: int, body: WeightBatchSelectReq,
                                  session: AsyncSession = Depends(get_session)) -> dict:
    """择优：把某成功候选记为批次显式最优。批次 / 候选缺失 404；非成功 / 空权重候选 409。"""
    row = await service.select_run(session, batch_id, body.runId)
    if row is None:
        raise HTTPException(404, detail=_NOT_FOUND)
    return row


@router.post("/weight-models/batches/{batch_id}/cancel")
async def cancel_weight_batch(batch_id: int,
                              session: AsyncSession = Depends(get_session)) -> Response:
    """整批取消：有在跑 / 排队 → 202 cancelling；已终态 → 200 幂等；未知批次 → 404。"""
    result = await service.cancel_batch(session, batch_id)
    if result == "not_found":
        raise HTTPException(404, detail=_NOT_FOUND)
    status_code = 202 if result == "cancelling" else 200
    return _json_response({"batchId": batch_id, "status": result}, status_code)


@router.post("/weight-models/batches/{batch_id}/candidates/{run_id}/cancel")
async def cancel_weight_batch_candidate(batch_id: int, run_id: int,
                                        session: AsyncSession = Depends(get_session)) -> Response:
    """取消单候选：running / queued → 202 cancelling；已终态 → 200 幂等；未知批次 / 候选 → 404。"""
    result = await service.cancel_candidate(session, batch_id, run_id)
    if result == "not_found":
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "训练批次或候选不存在"})
    status_code = 202 if result == "cancelling" else 200
    return _json_response({"batchId": batch_id, "runId": run_id, "status": result}, status_code)


@router.delete("/weight-models/batches/{batch_id}", status_code=204)
async def delete_weight_batch(batch_id: int,
                              session: AsyncSession = Depends(get_session)) -> Response:
    """删批次（级联删父模型 + 全部候选 + 批次行）：仍有在跑 / 排队候选 → 409（须先取消）。"""
    result = await service.delete_batch(session, batch_id)
    if result == "not_found":
        raise HTTPException(404, detail=_NOT_FOUND)
    if result == "running":
        raise HTTPException(409, detail={
            "code": "WEIGHT_BATCH_RUNNING", "message": "批次仍有候选在训练 / 排队，请先取消批次再删除"})
    return Response(status_code=204)


# ============================ 批次实时流（抄 ws_weight_train，R-06） ============================
@router.websocket("/ws/weight-batch/{batch_id}")
async def ws_weight_batch(websocket: WebSocket, batch_id: int) -> None:
    if not await guard_ws_origin(websocket):   # 必须在 accept() 之前：不合规连接一帧都不许收到
        return
    await websocket.accept()
    runtime = get_weight_batch_manager().get(batch_id)
    if runtime is None:
        await websocket.send_text(json.dumps({
            "type": "error", "data": {"code": "NOT_FOUND", "message": "训练批次不存在或已过期"}}))
        await websocket.close()
        return
    # subscribe() 与 snapshot() 间无 await：协作式单线程下原子，snapshot 为缓冲前缀、queue 为其后增量，不重复。
    queue = runtime.subscribe()
    snapshot = runtime.snapshot()
    try:
        for event in snapshot:
            await websocket.send_text(json.dumps(event))
        if snapshot and snapshot[-1].get("type") in _BATCH_TERMINAL:  # 已终态（快批次先于连线结束）→ 立即关闭
            await websocket.close()
            return
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_WS_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping"}))  # 静默超时 → ping 半开探测 + 终态兜底
                if runtime.is_terminal:
                    buf = runtime.snapshot()
                    if buf and buf[-1].get("type") in _BATCH_TERMINAL:  # 满队列若丢 batchDone，关闭前补发
                        await websocket.send_text(json.dumps(buf[-1]))
                    await websocket.close()
                    break
                continue
            await websocket.send_text(json.dumps(event))
            if event.get("type") in _BATCH_TERMINAL:
                await websocket.close()
                break
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        runtime.unsubscribe(queue)
