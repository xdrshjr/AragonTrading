"""路由：run 详情 / trades / equity (spec §9.1)。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import repositories as repo
from ..db.session import get_session
from ..schemas import equity_out, run_out, trade_out, walk_forward_out

router = APIRouter(tags=["runs"])


@router.get("/runs")
async def list_runs(
    strategyId: int | None = Query(None),
    instId: str | None = Query(None),
    mode: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """运行历史列表（F4）：按 id 降序，支持 strategy/inst/mode 过滤 + 分页。"""
    runs = await repo.list_runs(session, strategy_id=strategyId, inst_id=instId,
                                mode=mode, limit=limit, offset=offset)
    return [run_out(r) for r in runs]


@router.get("/runs/{run_id}")
async def get_run(run_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    run = await repo.get_run(session, run_id)
    if run is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "运行不存在"})
    return run_out(run)


@router.get("/runs/{run_id}/walk-forward")
async def get_walk_forward_run(run_id: int,
                               session: AsyncSession = Depends(get_session)) -> dict:
    """历史复看高级回测：从 config_json 还原 {runId, summary, windows}；非 walkforward → 404（spec §5.2）。"""
    run = await repo.get_run(session, run_id)
    if run is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "运行不存在"})
    if run.mode != "walkforward":
        raise HTTPException(404, detail={"code": "NOT_WALK_FORWARD", "message": "该运行不是高级回测"})
    return walk_forward_out(run)


@router.get("/runs/{run_id}/trades")
async def get_trades(run_id: int, limit: int = Query(500, ge=1, le=5000),
                     offset: int = Query(0, ge=0),
                     session: AsyncSession = Depends(get_session)) -> list[dict]:
    trades = await repo.list_trades(session, run_id, limit=limit, offset=offset)
    return [trade_out(t) for t in trades]


@router.get("/runs/{run_id}/equity")
async def get_equity(run_id: int, session: AsyncSession = Depends(get_session)) -> list[dict]:
    points = await repo.list_equity(session, run_id)
    return [equity_out(p) for p in points]
