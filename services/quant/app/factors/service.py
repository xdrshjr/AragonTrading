"""因子层 service 编排：取数（复用 load_range_candles）+ 线程池计算 + 规格快照 (spec §3.1)。

取数一律走 ``agent.services.load_range_candles``（真实 OKX、全区间分页、空区间 400），CPU 密集的
因子计算 / 评估 / 挖掘 / 训练 / 打分全部经 ``run_in_threadpool`` 调度（沿用 run_backtest 范式，R6）。
``resolve_set_to_spec`` 把保存的因子组合解析为 ``{members, thresholds}``，供创建 ``factor_score``
策略时**内联快照**进 ``params_json``（引擎零 IO，回测路径零改动，评审 C-P1-2）。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.services import load_range_candles
from ..config import get_settings
from ..db import repositories as repo
from ..db.models import FactorSet
from ..engine.backtester import candles_to_df
from ..engine.factors import (
    MINE_WORK_UNIT_LIMIT,
    build_factor_matrix,
    compose_score,
    compute_factor,
    default_candidates,
    eval_result_to_dict,
    evaluate_factor_key,
    factor_meta,
    forward_return_label,
    mine_factors,
    mining_workload,
    score_points,
)
from ..engine.factors.scoring import DEFAULT_NORM_WINDOW
from ..engine.mtf import bar_to_seconds
from .specs import (
    members_from_set_row,
    normalize_thresholds,
    thresholds_from_set_row,
    validate_factor_key,
    validate_members,
)


@dataclass(frozen=True)
class FactorWindow:
    """取数窗口（聚合传参，避免位置参数堆叠）。"""

    inst_id: str
    bar: str
    start_ts: int
    end_ts: int
    max_bars: int | None = None


async def _load_df(rest, window: FactorWindow) -> pd.DataFrame:
    """全区间分页取真实 OKX K 线 → 升序 DataFrame（空区间由 load_range_candles 抛 400）。"""
    candles = await load_range_candles(
        rest, inst_id=window.inst_id, bar=window.bar, start_ts=window.start_ts,
        end_ts=window.end_ts, max_bars=window.max_bars)
    return candles_to_df(candles)


def _series_points(df: pd.DataFrame, series: pd.Series) -> tuple[list[dict], float]:
    """因子值序列 → (``[{ts,value}]`` 剔 NaN, coverage)。"""
    arr = np.asarray(series, dtype=float)
    points = [{"ts": int(ts), "value": round(float(v), 6)}
              for ts, v in zip(df["ts"].to_numpy(), arr) if np.isfinite(v)]
    coverage = round(float(np.isfinite(arr).mean()), 4) if len(arr) else 0.0
    return points, coverage


# ============================ 计算 / 评估 / 挖掘 ============================
async def compute(rest, window: FactorWindow, *, factor_key: str, params: dict | None) -> dict:
    """计算单因子值序列（上图用）：``{instId,bar,points:[{ts,value}],coverage}``。"""
    validate_factor_key(factor_key)
    df = await _load_df(rest, window)
    series = await run_in_threadpool(compute_factor, df, factor_key, params, bar=window.bar)
    points, coverage = _series_points(df, series)
    return {"instId": window.inst_id, "bar": window.bar, "points": points, "coverage": coverage}


async def evaluate(session: AsyncSession, rest, window: FactorWindow, *, factor_key: str,
                   params: dict | None, direction: str, horizons: tuple[int, ...],
                   quantiles: int, factor_id: int | None = None) -> dict:
    """评估单因子（IC/RankIC/IR/分位）；给 ``factor_id`` 时把摘要缓存进 ``factors.eval_json``。"""
    validate_factor_key(factor_key)
    df = await _load_df(rest, window)
    result = await run_in_threadpool(
        evaluate_factor_key, df, factor_key, params, direction, bar=window.bar,
        horizons=horizons, quantiles=quantiles)
    result.instId = window.inst_id
    payload = eval_result_to_dict(result)
    if factor_id is not None:
        await repo.set_factor_eval(session, factor_id, payload)
    return payload


def _mined_factor_values(items: list[dict]) -> list[repo.FactorValues]:
    """把挖掘结果投影为可落库的 FactorValues（跳过 bar 不兼容项）。"""
    values: list[repo.FactorValues] = []
    for item in items:
        if item.get("note") == "bar_incompatible":
            continue
        meta = factor_meta(item["factorKey"]) or {}
        ir = item.get("ir")
        values.append(repo.FactorValues(
            factor_key=item["factorKey"], name=str(meta.get("cn") or item["factorKey"]),
            params=item.get("params") or {}, direction=item.get("direction", "long"),
            category=item.get("category", ""), source="mined",
            notes=f"IR={round(ir, 3)}" if ir is not None else "mined"))
    return values


async def mine(session: AsyncSession, rest, window: FactorWindow, *,
               categories: list[str] | None, horizons: tuple[int, ...], top_n: int,
               persist: bool) -> dict:
    """库 × 网格挖掘并按 RankIC IR 排名；工作量超限 413（绝不静默少算，评审 C-P1-3）。"""
    candidates = default_candidates(window.bar, categories)
    _guard_mine_workload(candidates, window)
    df = await _load_df(rest, window)
    ranked = await run_in_threadpool(mine_factors, df, candidates, bar=window.bar, horizons=horizons)
    items = ranked[:top_n]
    truncated = len(ranked) > top_n
    if persist and items:
        await repo.bulk_create_factors(session, _mined_factor_values(items))
    return {"items": items, "truncated": truncated}


def _guard_mine_workload(candidates: list[dict], window: FactorWindow) -> None:
    """候选数 × 有效根数超 ``MINE_WORK_UNIT_LIMIT`` → 413（沿用 compare 413 语义）。"""
    settings = get_settings()
    requested = (window.end_ts - window.start_ts) // bar_to_seconds(window.bar) + 1
    ceiling = settings.backtest_max_bars
    if window.max_bars is not None:
        ceiling = min(int(window.max_bars), ceiling)
    effective = max(0, min(int(requested), int(ceiling)))
    if mining_workload(len(candidates), effective) > MINE_WORK_UNIT_LIMIT:
        raise HTTPException(413, detail={
            "code": "FACTOR_MINE_TOO_LARGE",
            "message": "挖掘工作量过大，请缩短区间或限定类别后重试。"})


# ============================ 训练 / 打分 ============================
async def train(rest, window: FactorWindow, *, members: list[dict], method: str,
                config: dict | None, horizon: int) -> dict:
    """训练因子权重：缺 rf/xgb 库 → 400 FACTOR_TRAINER_UNAVAILABLE，绝不回假权重。"""
    members = validate_members(members)
    config = config or {}
    norm_window = int(config.get("normWindow", DEFAULT_NORM_WINDOW))
    df = await _load_df(rest, window)
    matrix, names, norm_stats = await run_in_threadpool(
        build_factor_matrix, df, members, norm_window=norm_window, bar=window.bar)
    label = forward_return_label(df, horizon)
    result = await _train_weights_guarded(matrix, label, names, method, config, norm_stats)
    if not result.available:
        raise HTTPException(400, detail={"code": "FACTOR_TRAINER_UNAVAILABLE", "message": result.note})
    return {"method": result.method, "weights": result.weights, "normStats": result.normStats,
            "metrics": result.metrics, "available": True, "note": result.note,
            "trainerVersion": result.trainerVersion}


async def _train_weights_guarded(matrix, label, names, method: str, config: dict, norm_stats: dict):
    """线程池训练，样本不足 / 未知方法的 ValueError 收敛为 400（C1 显式错误）。"""
    from ..engine.factors import train_weights
    try:
        return await run_in_threadpool(
            train_weights, matrix, label, names, method=method, config=config, norm_stats=norm_stats)
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "FACTOR_TRAIN_FAILED", "message": str(exc)}) from exc


def resolve_set_to_spec(row: FactorSet) -> dict:
    """因子组合行 → ``{members, thresholds}`` 自包含快照（创建 factor_score 策略时内联，C-P1-2）。"""
    return {"members": members_from_set_row(row), "thresholds": thresholds_from_set_row(row)}


async def score_set(rest, window: FactorWindow, row: FactorSet) -> dict:
    """对保存的因子组合算复合得分序列：``{points:[{ts,score}],latest,coverage}``。"""
    spec = resolve_set_to_spec(row)
    thresholds = spec["thresholds"]
    df = await _load_df(rest, window)
    score = await run_in_threadpool(
        compose_score, df, spec["members"], None,
        norm_window=int(thresholds.get("normWindow", DEFAULT_NORM_WINDOW)),
        squash=bool(thresholds.get("squash", True)), bar=window.bar)
    points = score_points(df, score)
    latest = points[-1]["score"] if points else None
    coverage = round(len(points) / len(df), 4) if len(df) else 0.0
    return {"points": points, "latest": latest, "coverage": coverage}


async def score_inline(rest, window: FactorWindow, *, members: list[dict],
                       thresholds: dict | None) -> dict:
    """对临时（未保存）内联组合算复合得分序列（FactorSetBuilder 预览用）。"""
    members = validate_members(members)
    resolved = normalize_thresholds(thresholds)
    df = await _load_df(rest, window)
    score = await run_in_threadpool(
        compose_score, df, members, None,
        norm_window=int(resolved.get("normWindow", DEFAULT_NORM_WINDOW)),
        squash=bool(resolved.get("squash", True)), bar=window.bar)
    points = score_points(df, score)
    latest = points[-1]["score"] if points else None
    coverage = round(len(points) / len(df), 4) if len(df) else 0.0
    return {"points": points, "latest": latest, "coverage": coverage}
