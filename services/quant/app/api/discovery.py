"""路由：策略与因子挖掘（strategy-factor-discovery §四）—— REST + WS 实时流。

字段 camelCase、时间秒级 UTC、错误体统一 ``{error:{code,message}}``（沿用 main.py 异常处理器）。
``get_rest_client`` 在本命名空间解析，故测试对 ``app.api.discovery.get_rest_client`` 的
monkeypatch 生效。

**WS 与既有端点的一处有意偏离（§4.2）**：``weight-batch`` 是「订阅即全量回放」，而挖掘 run 可能
跑三天、事件数十万条，全量回放不可行。本端点改为「**首发 ``state`` 全量快照 + 回放最近
``discovery_ws_ring`` 条事件**」；终态事件（``done``）单独持有一个槽位，**绝不被环形缓冲挤掉**，
保证晚订阅的客户端不会看到一个永远「进行中」的死流。
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_session
from ..discovery import promote as promote_service
from ..discovery import repositories as repo
from ..discovery import service
from ..discovery.manager import get_discovery_manager
from ..okx.rest_client import get_rest_client
from ..schemas import DiscoveryBacktestReq, DiscoveryStartReq

router = APIRouter(tags=["discovery"])

_TERMINAL_TYPES = ("done",)
_WS_HEARTBEAT_SECONDS = 20.0
_NOT_FOUND = {"code": "NOT_FOUND", "message": "挖掘任务不存在"}


def _json_response(payload: dict, status_code: int) -> Response:
    return Response(content=json.dumps(payload, ensure_ascii=False),
                    media_type="application/json", status_code=status_code)


# ============================ 任务 CRUD ============================
@router.post("/discovery/runs", status_code=201)
async def start_discovery(body: DiscoveryStartReq,
                          session: AsyncSession = Depends(get_session)) -> dict:
    """启动挖掘任务。守门序 403 → 400 → 413 → 409（见 ``service.start_run``）。"""
    return await service.start_run(session, get_rest_client(),
                                   body.model_dump(exclude_none=True))


@router.get("/discovery/runs")
async def list_discovery_runs(status: str = "", limit: int = Query(50, ge=1, le=200),
                              offset: int = Query(0, ge=0),
                              session: AsyncSession = Depends(get_session)) -> dict:
    rows, total = await repo.list_runs(session, status=status or None, limit=limit,
                                       offset=offset)
    return {"items": [service.run_summary(row) for row in rows], "total": total}


@router.get("/discovery/runs/{run_id}")
async def get_discovery_run(run_id: int,
                            session: AsyncSession = Depends(get_session)) -> dict:
    row = await repo.get_run(session, run_id)
    if row is None:
        raise HTTPException(404, detail=_NOT_FOUND)
    iterations = await repo.list_iterations(session, run_id, limit=20)
    return service.run_detail(row, iterations)


@router.post("/discovery/runs/{run_id}/pause")
async def pause_discovery_run(run_id: int,
                              session: AsyncSession = Depends(get_session)) -> Response:
    result = await service.pause_run(session, run_id)
    status_code = 200 if result["status"] in repo.TERMINAL_STATUSES else 202
    return _json_response(result, status_code)


@router.post("/discovery/runs/{run_id}/resume")
async def resume_discovery_run(run_id: int,
                               session: AsyncSession = Depends(get_session)) -> Response:
    return _json_response(await service.resume_run(session, get_rest_client(), run_id), 202)


@router.post("/discovery/runs/{run_id}/stop")
async def stop_discovery_run(run_id: int,
                             session: AsyncSession = Depends(get_session)) -> Response:
    result = await service.stop_run(session, run_id)
    return _json_response(result, 202 if result["status"] == "stopped" else 200)


@router.delete("/discovery/runs/{run_id}", status_code=204)
async def delete_discovery_run(run_id: int,
                               session: AsyncSession = Depends(get_session)) -> Response:
    result = await service.delete_run(session, run_id)
    if result == "not_found":
        raise HTTPException(404, detail=_NOT_FOUND)
    if result == "running":
        raise HTTPException(409, detail={
            "code": "DISCOVERY_RUNNING", "message": "任务仍在运行，请先停止后再删除"})
    return Response(status_code=204)


# ============================ 候选 ============================
@router.get("/discovery/runs/{run_id}/candidates")
async def list_discovery_candidates(run_id: int, verdict: str = "kept", mechanism: str = "",
                                    reason: str = "", sort: str = "", kind: str = "",
                                    limit: int = Query(50, ge=1, le=200),
                                    offset: int = Query(0, ge=0),
                                    session: AsyncSession = Depends(get_session)) -> dict:
    if await repo.get_run(session, run_id) is None:
        raise HTTPException(404, detail=_NOT_FOUND)
    rows, total = await repo.list_candidates(
        session, run_id, verdict=verdict or None, mechanism=mechanism, reason=reason,
        sort=sort, kind=kind, limit=limit, offset=offset)
    return {"items": [service.candidate_summary(row) for row in rows], "total": total}


@router.get("/discovery/candidates/{candidate_id}")
async def get_discovery_candidate(candidate_id: int,
                                  session: AsyncSession = Depends(get_session)) -> dict:
    row = await repo.get_candidate(session, candidate_id)
    if row is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "候选不存在"})
    return service.candidate_detail(row)


@router.post("/discovery/candidates/{candidate_id}/promote")
async def promote_discovery_candidate(candidate_id: int,
                                      session: AsyncSession = Depends(get_session)) -> dict:
    return await promote_service.promote_candidate(session, candidate_id)


@router.post("/discovery/candidates/{candidate_id}/backtest")
async def backtest_discovery_candidate(candidate_id: int, body: DiscoveryBacktestReq,
                                       session: AsyncSession = Depends(get_session)) -> dict:
    return await promote_service.backtest_candidate(
        session, get_rest_client(), candidate_id, start_ts=body.startTs, end_ts=body.endTs)


# ============================ 配置与词表 ============================
@router.get("/discovery/config/defaults")
async def discovery_defaults() -> dict:
    """全部阈值与预算默认值 + ``implementedAxes``（D-6：启动器只渲染这里回的轴）。"""
    return service.config_defaults()


@router.get("/discovery/vocabulary")
async def discovery_vocabulary() -> dict:
    return service.vocabulary_payload()


# ============================ 实时流 ============================
@router.websocket("/ws/discovery/{run_id}")
async def ws_discovery(websocket: WebSocket, run_id: int) -> None:
    await websocket.accept()
    run = get_discovery_manager().get(run_id)
    if run is None:
        await websocket.send_text(json.dumps({
            "type": "error",
            "data": {"code": "NOT_FOUND", "message": "挖掘任务未在运行（可查看历史记录）"}},
            ensure_ascii=False))
        await websocket.close()
        return
    # subscribe() 与 snapshot() 间无 await：协作式单线程下原子，snapshot 为缓冲前缀、queue 为其后增量。
    queue = run.subscribe()
    snapshot = run.snapshot()
    try:
        for event in snapshot:
            await websocket.send_text(json.dumps(event, ensure_ascii=False))
        if snapshot and snapshot[-1].get("type") in _TERMINAL_TYPES:
            await websocket.close()
            return
        await _pump(websocket, run, queue)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        run.unsubscribe(queue)


async def _pump(websocket: WebSocket, run, queue: asyncio.Queue) -> None:
    """转发增量事件；静默 20s 即 ping 半开探测，并在终态时补发 ``done`` 后关闭。"""
    while True:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=_WS_HEARTBEAT_SECONDS)
        except asyncio.TimeoutError:
            await websocket.send_text(json.dumps({"type": "ping"}))
            if run.is_terminal:
                buffered = run.snapshot()
                if buffered and buffered[-1].get("type") in _TERMINAL_TYPES:
                    await websocket.send_text(
                        json.dumps(buffered[-1], ensure_ascii=False))   # 满队列丢终态时补发
                await websocket.close()
                return
            continue
        await websocket.send_text(json.dumps(event, ensure_ascii=False))
        if event.get("type") in _TERMINAL_TYPES:
            await websocket.close()
            return
