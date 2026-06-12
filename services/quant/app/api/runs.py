"""路由：run 详情 / trades / equity (spec §9.1)。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import repositories as repo
from ..db.session import get_session
from ..schemas import equity_out, run_out, trade_out

router = APIRouter(tags=["runs"])


@router.get("/runs/{run_id}")
async def get_run(run_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    run = await repo.get_run(session, run_id)
    if run is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "运行不存在"})
    return run_out(run)


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
