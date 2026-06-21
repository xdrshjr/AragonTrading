"""可复用回测 / 对比 helper（从 api/strategies.py 抽取，UI 与 Agent 共用）(spec §3.1)。

为避免与现有测试的 monkeypatch 目标漂移，OKX 客户端经参数 `rest` 注入（调用方在各自
命名空间内解析 `get_rest_client`），故行为与原 router 逐字节一致（R8 等价性回归护栏）。
"""
from __future__ import annotations

import json
from collections.abc import Callable

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import repositories as repo
from ..db.models import StrategyRun
from ..engine.backtester import BacktestResult, run_backtest
from ..engine.mtf import bar_to_seconds
from ..okx.types import Candle
from ..schemas import equity_out
from ..strategies.base import BaseStrategy, get_strategy_class


# ============================ 取数（边界处显式错误，C1） ============================
def _filter_range_candles(candles: list[Candle], start_ts: int, end_ts: int) -> list[Candle]:
    return [c for c in candles if start_ts <= c.ts <= end_ts and getattr(c, "confirm", True)]


async def load_range_candles(rest, *, inst_id: str, bar: str, limit: int,
                             start_ts: int, end_ts: int) -> list[Candle]:
    """拉真实区间 K 线并过滤未确认 / 越界；空区间 → 400 EMPTY_RANGE。"""
    candles = await rest.get_candles(inst_id, bar, limit, before=end_ts, after=start_ts)
    filtered = _filter_range_candles(candles, start_ts, end_ts)
    if not filtered:
        raise HTTPException(400, detail={"code": "EMPTY_RANGE",
                                         "message": "No confirmed candles in requested range"})
    return filtered


async def load_htf_frames(rest, *, inst_id: str, start_ts: int, end_ts: int, base_seconds: int,
                          timeframes: list[str], htf_warmup_bars: int) -> dict[str, list[Candle]]:
    """对每个严格高于基础周期的 bar 拉真实 K 线（覆盖区间 + 暖机），过滤未确认与越界 (MTF §3.5)。"""
    frames: dict[str, list[Candle]] = {}
    for tf in timeframes:
        htf_seconds = bar_to_seconds(tf)
        if htf_seconds <= base_seconds:
            continue
        span_bars = (end_ts - start_ts) // htf_seconds + 1
        limit = min(1000, int(span_bars) + int(htf_warmup_bars))
        candles = await rest.get_candles(inst_id, tf, limit, before=end_ts + 1)
        frames[tf] = [c for c in candles if c.ts <= end_ts and getattr(c, "confirm", True)]
    return frames


# ============================ 回测编排（UI / Agent 共用） ============================
def _resolve_costs(settings, initial_capital, fees, slippage, leverage) -> tuple:
    return (
        initial_capital or settings.initial_capital,
        fees if fees is not None else settings.default_taker_fee_bps,
        slippage if slippage is not None else settings.default_slippage_bps,
        leverage if leverage is not None else settings.default_leverage,
    )


async def _prepare_strategy(session: AsyncSession, strategy_id: int,
                            params_override: dict | None):
    """取策略行 + 注册表类并实例化；未找到 / 模板失效抛显式 HTTPException。"""
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
    if params_override:
        params = {**params, **params_override}
    return row, cls(params)


async def _execute_and_persist(session: AsyncSession, *, strategy_id: int, strategy: BaseStrategy,
                               inst_id: str, bar: str, limit: int, start_ts: int, end_ts: int,
                               costs: tuple, candles: list[Candle],
                               htf_frames: dict[str, list[Candle]]) -> tuple[StrategyRun, BacktestResult]:
    """落 run → 线程池回测 → 落库结果（与 router 原行为一致，R8）。"""
    initial_capital, fees, slippage, leverage = costs
    run = await repo.create_run(
        session, strategy_id=strategy_id, inst_id=inst_id, bar=bar, mode="backtest",
        config={"initialCapital": initial_capital, "fees": fees, "slippage": slippage,
                "leverage": leverage, "limit": limit, "startTs": start_ts, "endTs": end_ts,
                "effectiveCandleCount": len(candles), "htfBars": list(htf_frames.keys())},
    )
    result = await run_in_threadpool(
        run_backtest, strategy, candles, bar,
        initial_capital=initial_capital, taker_fee_bps=fees, slippage_bps=slippage,
        leverage=leverage, htf_frames=htf_frames,
    )
    await repo.persist_backtest_result(
        session, run, trades=result.trades, equity=result.equity_points,
        metrics=result.metrics, start_ts=result.start_ts, end_ts=result.end_ts,
    )
    return run, result


async def run_single_backtest(session: AsyncSession, rest, *, strategy_id: int, inst_id: str,
                              bar: str, limit: int, start_ts: int, end_ts: int,
                              initial_capital: float | None = None, fees: float | None = None,
                              slippage: float | None = None, leverage: float | None = None,
                              params: dict | None = None) -> dict:
    """单策略回测：返回 {runId, metrics}（行为与原 /backtest 路由一致）。"""
    _row, strategy = await _prepare_strategy(session, strategy_id, params)
    costs = _resolve_costs(get_settings(), initial_capital, fees, slippage, leverage)
    candles = await load_range_candles(rest, inst_id=inst_id, bar=bar, limit=limit,
                                       start_ts=start_ts, end_ts=end_ts)
    await repo.upsert_candles(session, inst_id, bar, candles)

    base_seconds = bar_to_seconds(bar)
    tfs = [tf for tf in strategy.timeframes() if tf != bar]
    htf_frames: dict[str, list[Candle]] = {}
    if tfs:
        htf_frames = await load_htf_frames(rest, inst_id=inst_id, start_ts=start_ts, end_ts=end_ts,
                                           base_seconds=base_seconds, timeframes=tfs,
                                           htf_warmup_bars=strategy.htf_warmup_bars())
        for tf, cs in htf_frames.items():
            await repo.upsert_candles(session, inst_id, tf, cs)

    run, result = await _execute_and_persist(
        session, strategy_id=strategy_id, strategy=strategy, inst_id=inst_id, bar=bar,
        limit=limit, start_ts=start_ts, end_ts=end_ts, costs=costs, candles=candles,
        htf_frames=htf_frames)
    return {"runId": run.id, "metrics": result.metrics}


def _dedupe_ids(ids: list[int]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for sid in ids:
        if sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


async def _prepare_compare(session: AsyncSession, ordered_ids: list[int]) -> list[tuple]:
    prepared: list[tuple] = []
    for sid in ordered_ids:
        row = await repo.get_strategy_row(session, sid)
        if row is None:
            continue
        cls = get_strategy_class(row.template_key)
        if cls is None:
            continue
        try:
            params = {**cls.meta.default_params, **json.loads(row.params_json or "{}")}
        except json.JSONDecodeError:
            params = dict(cls.meta.default_params)
        prepared.append((sid, row, cls(params)))
    return prepared


async def run_compare(session: AsyncSession, rest, *, strategy_ids: list[int], inst_id: str,
                      bar: str, limit: int, start_ts: int, end_ts: int,
                      initial_capital: float | None = None, fees: float | None = None,
                      slippage: float | None = None, leverage: float | None = None,
                      cancel_check: Callable[[], bool] | None = None) -> dict:
    """多策略横向对比：候选 candles 与 HTF 并集各取一次，逐策略回测并各自落库（F3 / P1-2）。

    Args:
        cancel_check: 可选协作式取消探针（H11）；逐策略前命中即提前返回已完成项，余者跳过。
            UI router 不传（None → 不检查 → 行为与原路由逐字节一致，R8）。
    """
    costs = _resolve_costs(get_settings(), initial_capital, fees, slippage, leverage)
    candles = await load_range_candles(rest, inst_id=inst_id, bar=bar, limit=limit,
                                       start_ts=start_ts, end_ts=end_ts)
    await repo.upsert_candles(session, inst_id, bar, candles)

    prepared = await _prepare_compare(session, _dedupe_ids(strategy_ids))
    base_seconds = bar_to_seconds(bar)
    union_tfs = sorted({tf for _, _, s in prepared for tf in s.timeframes()
                        if tf != bar and bar_to_seconds(tf) > base_seconds})
    max_warmup = max((s.htf_warmup_bars() for _, _, s in prepared), default=60)
    union_frames: dict[str, list[Candle]] = {}
    if union_tfs:
        union_frames = await load_htf_frames(rest, inst_id=inst_id, start_ts=start_ts, end_ts=end_ts,
                                             base_seconds=base_seconds, timeframes=union_tfs,
                                             htf_warmup_bars=max_warmup)
        for tf, cs in union_frames.items():
            await repo.upsert_candles(session, inst_id, tf, cs)

    items: list[dict] = []
    for sid, row, strategy in prepared:
        if cancel_check is not None and cancel_check():
            break  # 协作式取消（H11）：返回已完成项，余下策略跳过。
        htf_frames = {tf: union_frames[tf] for tf in strategy.timeframes() if tf in union_frames}
        run, result = await _execute_and_persist(
            session, strategy_id=sid, strategy=strategy, inst_id=inst_id, bar=bar, limit=limit,
            start_ts=start_ts, end_ts=end_ts, costs=costs, candles=candles, htf_frames=htf_frames)
        items.append({
            "strategyId": sid,
            "name": row.name or strategy.meta.name,
            "runId": run.id,
            "metrics": result.metrics,
            "equity": [equity_out(e) for e in result.equity_points],
        })
    return {"instId": inst_id, "bar": bar, "items": items}
