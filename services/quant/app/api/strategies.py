"""路由：策略 CRUD + 触发回测 (spec §9.1)。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import repositories as repo
from ..db.session import get_session
from ..engine.backtester import run_backtest
from ..okx.rest_client import get_rest_client
from ..schemas import BacktestReq, CreateStrategyReq, UpdateStrategyReq
from ..strategies.base import get_strategy_class

router = APIRouter(tags=["strategies"])


@router.get("/strategies")
async def list_strategies(enabled: bool | None = Query(None),
                          session: AsyncSession = Depends(get_session)) -> list[dict]:
    return await repo.list_strategies(session, enabled)


@router.get("/strategies/{strategy_id}")
async def get_strategy(strategy_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    s = await repo.get_strategy(session, strategy_id)
    if s is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "策略不存在"})
    return s


@router.post("/strategies", status_code=201)
async def create_strategy(body: CreateStrategyReq,
                          session: AsyncSession = Depends(get_session)) -> dict:
    if get_strategy_class(body.templateKey) is None:
        raise HTTPException(400, detail={"code": "BAD_TEMPLATE", "message": "模板不存在"})
    return await repo.create_strategy(session, body.templateKey, body.name, body.params)


@router.put("/strategies/{strategy_id}")
async def update_strategy(strategy_id: int, body: UpdateStrategyReq,
                          session: AsyncSession = Depends(get_session)) -> dict:
    s = await repo.update_strategy(session, strategy_id, name=body.name,
                                   params=body.params, enabled=body.enabled)
    if s is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "策略不存在"})
    return s


@router.delete("/strategies/{strategy_id}", status_code=204)
async def delete_strategy(strategy_id: int, session: AsyncSession = Depends(get_session)) -> Response:
    result = await repo.delete_strategy(session, strategy_id)
    if result == "not_found":
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "策略不存在"})
    if result == "builtin":
        raise HTTPException(409, detail={"code": "BUILTIN_READONLY",
                                         "message": "内置模板不可删除，请改用'另存为'或禁用"})
    return Response(status_code=204)


@router.post("/strategies/{strategy_id}/backtest")
async def backtest(strategy_id: int, body: BacktestReq,
                   session: AsyncSession = Depends(get_session)) -> dict:
    row = await repo.get_strategy_row(session, strategy_id)
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
    fees = body.fees if body.fees is not None else settings.default_taker_fee_bps
    slippage = body.slippage if body.slippage is not None else settings.default_slippage_bps
    leverage = body.leverage if body.leverage is not None else settings.default_leverage

    # 取真实 K 线（OKX 不可达直接抛 OKXError -> 5xx 显式错误，C1）
    candles = await get_rest_client().get_candles(body.instId, body.bar, body.limit)
    await repo.upsert_candles(session, body.instId, body.bar, candles)

    run = await repo.create_run(
        session, strategy_id=strategy_id, inst_id=body.instId, bar=body.bar, mode="backtest",
        config={"initialCapital": initial_capital, "fees": fees, "slippage": slippage,
                "leverage": leverage, "limit": body.limit},
    )

    # 回测纯计算放线程池，避免阻塞事件循环 (R9 / C12)
    result = await run_in_threadpool(
        run_backtest, strategy, candles, body.bar,
        initial_capital=initial_capital, taker_fee_bps=fees, slippage_bps=slippage,
        leverage=leverage,
    )
    await repo.persist_backtest_result(
        session, run, trades=result.trades, equity=result.equity_points,
        metrics=result.metrics, start_ts=result.start_ts, end_ts=result.end_ts,
    )
    return {"runId": run.id, "metrics": result.metrics}
