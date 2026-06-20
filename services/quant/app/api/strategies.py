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
from ..engine.mtf import bar_to_seconds
from ..okx.types import Candle
from ..okx.rest_client import get_rest_client
from ..schemas import BacktestReq, CompareReq, CreateStrategyReq, UpdateStrategyReq, equity_out
from ..strategies.base import BaseStrategy, get_strategy_class

router = APIRouter(tags=["strategies"])


def _filter_backtest_candles(candles: list[Candle], start_ts: int, end_ts: int) -> list[Candle]:
    return [
        c for c in candles
        if start_ts <= c.ts <= end_ts and getattr(c, "confirm", True)
    ]


async def _load_backtest_candles(body: BacktestReq | CompareReq) -> list[Candle]:
    candles = await get_rest_client().get_candles(
        body.instId, body.bar, body.limit, before=body.endTs, after=body.startTs,
    )
    filtered = _filter_backtest_candles(candles, body.startTs, body.endTs)
    if not filtered:
        raise HTTPException(400, detail={"code": "EMPTY_RANGE", "message": "No confirmed candles in requested range"})
    return filtered


async def _load_htf_frames(body: BacktestReq | CompareReq, base_seconds: int,
                           timeframes: list[str],
                           htf_warmup_bars: int) -> dict[str, list[Candle]]:
    """对每个高周期 bar 拉真实 OKX K 线，覆盖回测区间 + 暖机，过滤未确认与越界 (MTF §3.5)。

    取数失败沿用 `get_candles` 抛出的 `OKXError` → 5xx 显式错误（C1，绝不假数据）。
    """
    frames: dict[str, list[Candle]] = {}
    for tf in timeframes:
        htf_seconds = bar_to_seconds(tf)
        if htf_seconds <= base_seconds:        # 必须严格高于基础周期
            continue
        span_bars = (body.endTs - body.startTs) // htf_seconds + 1
        # 【P2-1 不变量】limit 上限 1000：因 BacktestReq.limit ≤ 1000，基础区间最多 1000×base_seconds；
        # HTF 取 min(1000, span+warmup) 根含 warmup 缓冲，可证 min(1000)×htf_seconds ≥ 1000×base_seconds，
        # 故 HTF 时间跨度恒 ≥ 基础区间，不会因 1000 截断而留早段 NaN。放宽基础 limit 时须同步复核。
        limit = min(1000, int(span_bars) + int(htf_warmup_bars))
        candles = await get_rest_client().get_candles(
            body.instId, tf, limit, before=body.endTs + 1)   # before=「早于」语义→左滚回溯
        frames[tf] = [c for c in candles
                      if c.ts <= body.endTs and getattr(c, "confirm", True)]
    return frames


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
    candles = await _load_backtest_candles(body)
    await repo.upsert_candles(session, body.instId, body.bar, candles)

    # 多周期：按策略声明拉真实高周期 K 线并入缓存复用（MTF §3.5）
    tfs = [tf for tf in strategy.timeframes() if tf != body.bar]
    htf_frames: dict[str, list[Candle]] = {}
    if tfs:
        htf_frames = await _load_htf_frames(
            body, bar_to_seconds(body.bar), tfs, strategy.htf_warmup_bars())
        for tf, cs in htf_frames.items():
            await repo.upsert_candles(session, body.instId, tf, cs)

    run = await repo.create_run(
        session, strategy_id=strategy_id, inst_id=body.instId, bar=body.bar, mode="backtest",
        config={"initialCapital": initial_capital, "fees": fees, "slippage": slippage,
                "leverage": leverage, "limit": body.limit, "startTs": body.startTs,
                "endTs": body.endTs, "effectiveCandleCount": len(candles),
                "htfBars": list(htf_frames.keys())},
    )

    # 回测纯计算放线程池，避免阻塞事件循环 (R9 / C12)
    result = await run_in_threadpool(
        run_backtest, strategy, candles, body.bar,
        initial_capital=initial_capital, taker_fee_bps=fees, slippage_bps=slippage,
        leverage=leverage, htf_frames=htf_frames,
    )
    await repo.persist_backtest_result(
        session, run, trades=result.trades, equity=result.equity_points,
        metrics=result.metrics, start_ts=result.start_ts, end_ts=result.end_ts,
    )
    return {"runId": run.id, "metrics": result.metrics}


@router.post("/strategies/compare")
async def compare_strategies(body: CompareReq,
                             session: AsyncSession = Depends(get_session)) -> dict:
    """多策略横向对比（F3）：候选 candles 只取一次复用于全部策略，逐一回测并各自落库。"""
    # strategyIds 去重（保序），重复 id 不重复落库
    seen: set[int] = set()
    ordered_ids: list[int] = []
    for sid in body.strategyIds:
        if sid not in seen:
            seen.add(sid)
            ordered_ids.append(sid)

    settings = get_settings()
    initial_capital = body.initialCapital or settings.initial_capital
    fees = body.fees if body.fees is not None else settings.default_taker_fee_bps
    slippage = body.slippage if body.slippage is not None else settings.default_slippage_bps
    leverage = body.leverage if body.leverage is not None else settings.default_leverage

    # 候选 candles 只取一次（复用于全部策略，避免 N× OKX 调用触发限频，R1）
    candles = await _load_backtest_candles(body)
    await repo.upsert_candles(session, body.instId, body.bar, candles)

    # —— 第一段：预实例化有效候选（保序、去重 id 后），缓存 (sid, row, strategy)（P1-2）——
    prepared: list[tuple[int, object, BaseStrategy]] = []
    for sid in ordered_ids:
        row = await repo.get_strategy_row(session, sid)
        if row is None:
            continue   # 跳过不存在的策略，不整体失败
        cls = get_strategy_class(row.template_key)
        if cls is None:
            continue   # 模板失效则跳过
        try:
            params = {**cls.meta.default_params, **json.loads(row.params_json or "{}")}
        except json.JSONDecodeError:
            params = dict(cls.meta.default_params)
        prepared.append((sid, row, cls(params)))

    # —— 第二段：并集取数（只取一次，T8 断言 REST 调用计数为并集大小）——
    base_seconds = bar_to_seconds(body.bar)
    union_tfs = sorted({tf for _, _, s in prepared for tf in s.timeframes()
                        if tf != body.bar and bar_to_seconds(tf) > base_seconds})
    max_warmup = max((s.htf_warmup_bars() for _, _, s in prepared), default=60)
    union_frames: dict[str, list[Candle]] = {}
    if union_tfs:
        union_frames = await _load_htf_frames(body, base_seconds, union_tfs, max_warmup)
        for tf, cs in union_frames.items():
            await repo.upsert_candles(session, body.instId, tf, cs)

    # —— 第三段：循环复用实例，逐策略只下发其声明子集，各自落库 config.htfBars（P2-2）——
    items: list[dict] = []
    for sid, row, strategy in prepared:
        htf_frames = {tf: union_frames[tf] for tf in strategy.timeframes()
                      if tf in union_frames}
        run = await repo.create_run(
            session, strategy_id=sid, inst_id=body.instId, bar=body.bar, mode="backtest",
            config={"initialCapital": initial_capital, "fees": fees, "slippage": slippage,
                    "leverage": leverage, "limit": body.limit, "startTs": body.startTs,
                    "endTs": body.endTs, "effectiveCandleCount": len(candles),
                    "htfBars": list(htf_frames.keys())},
        )
        result = await run_in_threadpool(
            run_backtest, strategy, candles, body.bar,
            initial_capital=initial_capital, taker_fee_bps=fees, slippage_bps=slippage,
            leverage=leverage, htf_frames=htf_frames,
        )
        await repo.persist_backtest_result(
            session, run, trades=result.trades, equity=result.equity_points,
            metrics=result.metrics, start_ts=result.start_ts, end_ts=result.end_ts,
        )
        items.append({
            "strategyId": sid,
            "name": row.name or strategy.meta.name,
            "runId": run.id,
            "metrics": result.metrics,
            "equity": [equity_out(e) for e in result.equity_points],
        })

    return {"instId": body.instId, "bar": body.bar, "items": items}
