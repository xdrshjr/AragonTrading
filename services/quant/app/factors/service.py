"""因子层 service 编排：取数（复用 load_range_candles）+ 线程池计算 + 规格快照 (spec §3.1)。

取数一律走 ``agent.services.load_range_candles``（真实 OKX、全区间分页、空区间 400），CPU 密集的
因子计算 / 评估 / 挖掘 / 训练 / 打分全部经 ``run_in_threadpool`` 调度（沿用 run_backtest 范式，R6）。
``resolve_set_to_spec`` 把保存的因子组合解析为 ``{members, thresholds}``，供创建 ``factor_score``
策略时**内联快照**进 ``params_json``（引擎零 IO，回测路径零改动，评审 C-P1-2）。
"""
from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.services import load_range_candles
from ..config import get_settings
from ..db import repositories as repo
from ..db.models import FactorSet, Strategy
from ..engine.backtester import candles_to_df
from ..engine.factors import (
    MINE_WORK_UNIT_LIMIT,
    aggregate_eval_results,
    aggregate_mine_results,
    build_factor_matrix,
    build_pooled_sample,
    compose_score,
    compute_factor,
    default_candidates,
    eval_result_to_dict,
    evaluate_factor_key,
    factor_meta,
    forward_return_label,
    is_bar_compatible,
    mine_factors,
    mining_workload,
    resolve_norm_contract,
    score_points,
    train_weights_on_sample,
)
from ..engine.factors.mining import MINE_MIN_COVERAGE, MINE_PERSIST_MIN_ABS_IR
from ..engine.factors.scoring import DEFAULT_CLIP, DEFAULT_NORM_WINDOW
from ..engine.mtf import bar_to_seconds
from ..schemas import factor_set_out
from .specs import (
    assert_norm_contract,
    assert_weights_cover,
    members_from_set_row,
    norm_stats_from_set_row,
    normalize_thresholds,
    provenance_from_set_row,
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


# 多步因子工作流（mine→evaluate→train→score）同窗取数复用：有界、短 TTL、**仅缓存成功取到的真实
# K 线**的进程内缓存（L6）。历史 K 线不可变，blast radius 小；未命中仍真实取数、OKX 不可达仍由
# load_range_candles 显式报错（绝不以缓存假装实时）。命中返回 ``.copy()`` 防下游就地改列污染缓存（评审 P1-3）。
_CANDLE_CACHE_TTL_SECONDS = 90.0
_CANDLE_CACHE_CAP = 16
_candle_cache: "OrderedDict[tuple, tuple[float, pd.DataFrame]]" = OrderedDict()


def clear_candle_cache() -> None:
    """清空进程内取数缓存（测试隔离用：测试共享进程，须能清缓存防串扰，呼应 aragon-quant-test-isolation）。"""
    _candle_cache.clear()


def _candle_cache_key(window: FactorWindow) -> tuple:
    return (window.inst_id, window.bar, window.start_ts, window.end_ts, window.max_bars)


async def _load_df(rest, window: FactorWindow) -> pd.DataFrame:
    """全区间分页取真实 OKX K 线 → 升序 DataFrame（空区间由 load_range_candles 抛 400）。

    命中未过期缓存即复用（返回防御性拷贝）；未命中走真实取数后写缓存并按 LRU+TTL 淘汰（L6）。
    """
    key = _candle_cache_key(window)
    now = time.monotonic()
    cached = _candle_cache.get(key)
    if cached is not None:
        cached_at, cached_df = cached
        if now - cached_at <= _CANDLE_CACHE_TTL_SECONDS:
            _candle_cache.move_to_end(key)
            return cached_df.copy()
        del _candle_cache[key]
    candles = await load_range_candles(
        rest, inst_id=window.inst_id, bar=window.bar, start_ts=window.start_ts,
        end_ts=window.end_ts, max_bars=window.max_bars)
    df = candles_to_df(candles)
    _candle_cache[key] = (now, df)
    _candle_cache.move_to_end(key)
    while len(_candle_cache) > _CANDLE_CACHE_CAP:
        _candle_cache.popitem(last=False)
    return df.copy()


def _series_points(df: pd.DataFrame, series: pd.Series) -> tuple[list[dict], float]:
    """因子值序列 → (``[{ts,value}]`` 剔 NaN, coverage)。"""
    arr = np.asarray(series, dtype=float)
    points = [{"ts": int(ts), "value": round(float(v), 6)}
              for ts, v in zip(df["ts"].to_numpy(), arr) if np.isfinite(v)]
    coverage = round(float(np.isfinite(arr).mean()), 4) if len(arr) else 0.0
    return points, coverage


# ============================ 计算 / 评估 / 挖掘 ============================
def _members_bar_incompatible(members: list[dict], bar: str) -> bool:
    """组合任一成员对当前 bar 不兼容（日内因子用于非日内 bar → 全 NaN）（L5）。"""
    return any(not is_bar_compatible(m.get("factorKey", ""), bar) for m in members)


async def compute(rest, window: FactorWindow, *, factor_key: str, params: dict | None) -> dict:
    """计算单因子值序列（上图用）：``{instId,bar,points:[{ts,value}],coverage[,note]}``。"""
    validate_factor_key(factor_key)
    df = await _load_df(rest, window)
    series = await run_in_threadpool(compute_factor, df, factor_key, params, bar=window.bar)
    points, coverage = _series_points(df, series)
    out = {"instId": window.inst_id, "bar": window.bar, "points": points, "coverage": coverage}
    # 日内限定因子用于非日内 bar → 全 NaN；如实标注而非静默空白（L5）。
    if not is_bar_compatible(factor_key, window.bar):
        out["note"] = "bar_incompatible"
    return out


def _effective_symbols(symbols: list[str] | None, focus: str) -> list[str]:
    """去空去重后的有效标的（保序，[0] 为焦点）；缺省 / 全空回落焦点单标的（factor-lab-symbol-groups §3.3）。"""
    if not symbols:
        return [focus]
    out: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        inst = (raw or "").strip()
        if inst and inst not in seen:
            seen.add(inst)
            out.append(inst)
    return out or [focus]


def _guard_eval_symbols(symbols: list[str]) -> None:
    """评估标的数超 ``max_eval_symbols`` → 400（**取数前**判定，绝不无界扇出 OKX 请求，C-1）。"""
    limit = get_settings().max_eval_symbols
    if len(symbols) > limit:
        raise HTTPException(400, detail={
            "code": "FACTOR_EVAL_TOO_MANY_SYMBOLS",
            "message": f"标的组过大：评估最多支持 {limit} 个标的，请缩减标的组后重试。"})


async def _evaluate_one(rest, window: FactorWindow, inst_id: str, *, factor_key: str,
                        params: dict | None, direction: str, horizons: tuple[int, ...],
                        quantiles: int) -> dict:
    """对单标的取真实 OKX 数据并评估 → payload（``instId`` 落该标的；bar 不兼容如实标注）。"""
    df = await _load_df(rest, replace(window, inst_id=inst_id))
    result = await run_in_threadpool(
        evaluate_factor_key, df, factor_key, params, direction, bar=window.bar,
        horizons=horizons, quantiles=quantiles)
    result.instId = inst_id
    payload = eval_result_to_dict(result)
    if not is_bar_compatible(factor_key, window.bar):
        payload["note"] = "bar_incompatible"
    return payload


async def evaluate(session: AsyncSession, rest, window: FactorWindow, *, factor_key: str,
                   params: dict | None, direction: str, horizons: tuple[int, ...],
                   quantiles: int, factor_id: int | None = None,
                   symbols: list[str] | None = None) -> dict:
    """评估单因子 / 标的组（IC/RankIC/IR/分位）；给 ``factor_id`` 时把摘要缓存进 ``factors.eval_json``。

    单标的（含缺省 ``symbols``）走原路径，行为字节级不变（回归护栏）。标的组（``len>1``）先扇出护栏
    （C-1）→ 焦点标的解析并冻结方向一次（C-2）→ 其余标的以冻结方向逐一取真实数据评估 → occurrences
    加权聚合（``aggregate_eval_results``）。逐标的贡献经 ``perSymbol`` 可见，绝不静默丢弃标的。
    """
    validate_factor_key(factor_key)
    effective = _effective_symbols(symbols, window.inst_id)
    if len(effective) == 1:
        payload = await _evaluate_one(
            rest, window, effective[0], factor_key=factor_key, params=params, direction=direction,
            horizons=horizons, quantiles=quantiles)
        if factor_id is not None:
            await repo.set_factor_eval(session, factor_id, payload)
        return payload
    # 标的组：取数前扇出护栏 → 焦点标的一次性解析冻结方向（auto 仅在此定号）→ 逐标的同向评估 → 聚合。
    _guard_eval_symbols(effective)
    focus_payload = await _evaluate_one(
        rest, window, effective[0], factor_key=factor_key, params=params, direction=direction,
        horizons=horizons, quantiles=quantiles)
    frozen = focus_payload["direction"]   # evaluate_factor 已把 auto 定号为 long/short（C-2 焦点锚点）
    results = [focus_payload]
    for sym in effective[1:]:
        results.append(await _evaluate_one(
            rest, window, sym, factor_key=factor_key, params=params, direction=frozen,
            horizons=horizons, quantiles=quantiles))
    aggregated = aggregate_eval_results(results, effective, frozen)
    if factor_id is not None:
        await repo.set_factor_eval(session, factor_id, aggregated)
    return aggregated


def _mined_factor_values(items: list[dict]) -> list[repo.FactorValues]:
    """把挖掘结果投影为可落库的 FactorValues（过滤无效项，绝不静默落假因子）（H5）。

    跳过：bar 不兼容、``ir is None``（无预测力/样本不足）、``abs(ir) < MINE_PERSIST_MIN_ABS_IR``、
    ``coverage < MINE_MIN_COVERAGE``。去重由 ``repo.bulk_create_factors`` 在落库前 query-before-insert 负责。
    """
    values: list[repo.FactorValues] = []
    for item in items:
        if item.get("note") == "bar_incompatible":
            continue
        ir = item.get("ir")
        if ir is None or abs(float(ir)) < MINE_PERSIST_MIN_ABS_IR:
            continue
        if float(item.get("coverage") or 0.0) < MINE_MIN_COVERAGE:
            continue
        meta = factor_meta(item["factorKey"]) or {}
        values.append(repo.FactorValues(
            factor_key=item["factorKey"], name=str(meta.get("cn") or item["factorKey"]),
            params=item.get("params") or {}, direction=item.get("direction", "long"),
            category=item.get("category", ""), source="mined",
            notes=f"IR={round(float(ir), 3)}"))
    return values


async def mine(session: AsyncSession, rest, window: FactorWindow, *,
               categories: list[str] | None, horizons: tuple[int, ...], top_n: int,
               persist: bool, symbols: list[str] | None = None) -> dict:
    """库 × 网格挖掘并按 RankIC IR 排名；工作量超限 413（绝不静默少算，评审 C-P1-3）。

    单标的走原路径。标的组（``len>1``）逐标的取真实数据挖掘 → 按 ``(factorKey, canonical(params))`` 归并
    （方向统一取焦点标的，C-3），组分 = 各标的 IR coverage 加权均值。413 工作量护栏**计入标的数**且在
    ``_load_df`` **之前**执行，故大组先 413、再取数，扇出天然受限（C-1/C-3）。
    """
    candidates = default_candidates(window.bar, categories)
    effective = _effective_symbols(symbols, window.inst_id)
    _guard_mine_workload(candidates, window, symbol_count=len(effective))
    if len(effective) == 1:
        df = await _load_df(rest, window)
        ranked = await run_in_threadpool(mine_factors, df, candidates, bar=window.bar, horizons=horizons)
    else:
        per_symbol: list[list[dict]] = []
        for sym in effective:
            df = await _load_df(rest, replace(window, inst_id=sym))
            per_symbol.append(await run_in_threadpool(
                mine_factors, df, candidates, bar=window.bar, horizons=horizons))
        # 归并取全量（top_n 截断留待下方与单标的同口径处理，len(candidates) 上界即「不截断」）。
        ranked = await run_in_threadpool(
            aggregate_mine_results, per_symbol, effective, len(candidates))
    items = ranked[:top_n]
    truncated = len(ranked) > top_n
    persisted = 0
    if persist and items:
        inserted = await repo.bulk_create_factors(session, _mined_factor_values(items))
        persisted = len(inserted)
    # 计数透明：evaluated=本次考虑落库的候选数；persisted=实际新写入；skipped=去重+无效（绝不静默丢弃，H5）。
    skipped = (len(items) - persisted) if persist else 0
    out = {"items": items, "truncated": truncated, "evaluated": len(items),
           "persisted": persisted, "skipped": skipped}
    if len(effective) > 1:
        out["symbols"] = effective   # 回显聚合标的（前端组态提示）
    return out


def _guard_mine_workload(candidates: list[dict], window: FactorWindow, *,
                         symbol_count: int = 1) -> None:
    """候选数 × 有效根数 × 标的数超 ``MINE_WORK_UNIT_LIMIT`` → 413（沿用 compare 413 语义；标的组计入扇出，C-3）。"""
    settings = get_settings()
    requested = (window.end_ts - window.start_ts) // bar_to_seconds(window.bar) + 1
    ceiling = settings.backtest_max_bars
    if window.max_bars is not None:
        ceiling = min(int(window.max_bars), ceiling)
    effective = max(0, min(int(requested), int(ceiling)))
    if mining_workload(len(candidates), effective) * max(1, int(symbol_count)) > MINE_WORK_UNIT_LIMIT:
        raise HTTPException(413, detail={
            "code": "FACTOR_MINE_TOO_LARGE",
            "message": "挖掘工作量过大，请缩短区间、限定类别或缩减标的组后重试。"})


# ============================ 训练 / 打分 ============================
def _guard_train_workload(window: FactorWindow) -> None:
    """同步训练有效根数超 ``factor_train_max_bars`` → 413（绝不让无界长请求被传输层切断成 socket hang up）。

    与 ``mine`` 不同：train 把 ≤12 个成员合成单矩阵、无候选维度，故以纯「有效根数」度量（照搬
    ``mining_workload`` 的候选数×根数会因 12×20000≪3M 而成 no-op 护栏）。有效根数口径与
    ``load_range_candles``/``_guard_mine_workload`` 一致（``backtest_max_bars`` 二次夹取、``expected≤0`` 归零）。
    """
    settings = get_settings()
    requested = (window.end_ts - window.start_ts) // bar_to_seconds(window.bar) + 1
    ceiling = settings.backtest_max_bars
    if window.max_bars is not None:
        ceiling = min(int(window.max_bars), ceiling)
    effective = max(0, min(int(requested), int(ceiling)))
    if effective > settings.factor_train_max_bars:
        raise HTTPException(413, detail={
            "code": "FACTOR_TRAIN_TOO_LARGE",
            "message": "训练区间过长，请缩短区间（或调高上限）后重试。"})


async def train(rest, window: FactorWindow, *, members: list[dict], method: str,
                config: dict | None, horizon: int, symbols: list[str] | None = None) -> dict:
    """训练因子权重：区间过长 → 413 FACTOR_TRAIN_TOO_LARGE；缺 rf/xgb 库 → 400 FACTOR_TRAINER_UNAVAILABLE，绝不回假权重。

    单标的走原路径（``train_weights``，字节级不变，等价性门禁保持）。标的组（``len>1``）逐标的
    ``build_factor_matrix``（各自因果 z-score，绝不跨标的拼原始 K 线）→ ``build_pooled_sample`` 池化 →
    ``train_weights_on_sample`` 末步装配，绝不改被门禁守护的 ``train_weights`` / ``build_train_result``。
    """
    members = validate_members(members)
    config = config or {}
    norm_window = int(config.get("normWindow", DEFAULT_NORM_WINDOW))
    clip = float(config.get("clip", DEFAULT_CLIP))
    effective = _effective_symbols(symbols, window.inst_id)
    _guard_train_workload(window)
    if len(effective) == 1:
        df = await _load_df(rest, window)
        matrix, names, norm_stats = await run_in_threadpool(
            build_factor_matrix, df, members, norm_window=norm_window, clip=clip, bar=window.bar)
        label = forward_return_label(df, horizon)
        result = await _train_weights_guarded(matrix, label, names, method, config, norm_stats)
    else:
        parts: list[tuple] = []
        names, norm_stats = [], {}
        for sym in effective:
            df = await _load_df(rest, replace(window, inst_id=sym))
            matrix, names, norm_stats = await run_in_threadpool(
                build_factor_matrix, df, members, norm_window=norm_window, clip=clip, bar=window.bar)
            parts.append((matrix, forward_return_label(df, horizon)))
        result = await _pooled_train_guarded(parts, names, method, config, norm_stats)
    if not result.available:
        raise HTTPException(400, detail={"code": "FACTOR_TRAINER_UNAVAILABLE", "message": result.note})
    # 溯源元数据（H7）：取数窗口 + horizon；trainedAt 由前端/路由注入秒级 UTC（引擎/服务不取时钟）。
    provenance = {"instId": window.inst_id, "bar": window.bar, "startTs": window.start_ts,
                  "endTs": window.end_ts, "horizon": int(horizon)}
    if len(effective) > 1:
        provenance["symbols"] = effective
    return {"method": result.method, "weights": result.weights, "normStats": result.normStats,
            "metrics": result.metrics, "available": True, "note": result.note,
            "trainerVersion": result.trainerVersion, "provenance": provenance}


async def _train_weights_guarded(matrix, label, names, method: str, config: dict, norm_stats: dict):
    """线程池训练，样本不足 / 未知方法的 ValueError 收敛为 400（C1 显式错误）。"""
    from ..engine.factors import train_weights
    try:
        return await run_in_threadpool(
            train_weights, matrix, label, names, method=method, config=config, norm_stats=norm_stats)
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "FACTOR_TRAIN_FAILED", "message": str(exc)}) from exc


async def _pooled_train_guarded(parts, names, method: str, config: dict, norm_stats: dict):
    """多标的池化训练（线程池）：池化不足 / 样本不足 / 未知方法的 ValueError 收敛为 400（C1 显式错误）。"""
    try:
        sample = await run_in_threadpool(build_pooled_sample, parts, config)
        return await run_in_threadpool(
            train_weights_on_sample, sample, names, method=method, config=config, norm_stats=norm_stats)
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "FACTOR_TRAIN_FAILED", "message": str(exc)}) from exc


# ============================ 组合 ↔ 伴生策略 单事务同步编排（R2/R3/R5） ============================
def _set_out_with_strategy(row: FactorSet, strategy_id: int | None) -> dict:
    """``factor_set_out`` 纯函数输出 + 读路径附加的 ``strategyId``（无伴生为 ``None``，R1）。

    ``factor_set_out`` 本体保持纯同步、不增 IO；``strategyId`` 一律在 service / 路由包装层附加。
    """
    return {**factor_set_out(row), "strategyId": strategy_id}


def _companion_name(set_row: FactorSet) -> str:
    """伴生策略名称 = ``{组合名} · 得分策略``（与前端旧手动建策略口径一致，便于识别）。"""
    return f"{set_row.name} · 得分策略"


async def ensure_companion_strategy(session: AsyncSession, set_row: FactorSet) -> Strategy:
    """把组合的伴生 ``factor_score`` 策略收敛到「正典（min id）」并以组合快照刷新（R2）。

    ``list_factor_score_rows_for_set`` 按 ``id`` 升序 → 取首条为正典，``apply_strategy`` 以
    ``resolve_set_to_spec(set_row) + {factorSetId}`` 刷新其 ``params``、以 ``{name} · 得分策略`` 刷新
    ``name``；其余重复伴生一并 ``session.delete``（历史多伴生坍缩为恰好一条）。一条都没有则
    ``add_strategy`` 补建（新建 / 自愈）。**不触 ``enabled``（保留用户启停，R9），不在此 commit**
    （提交收口在编排末尾，保证原子性）。
    """
    name = _companion_name(set_row)
    params = {**resolve_set_to_spec(set_row), "factorSetId": set_row.id}
    rows = await repo.list_factor_score_rows_for_set(session, set_row.id)
    if not rows:
        return await repo.add_strategy(session, "factor_score", name, params)
    canonical, *extras = rows
    await repo.apply_strategy(session, canonical, name=name, params=params)
    for extra in extras:
        await session.delete(extra)
    return canonical


async def create_set(session: AsyncSession, *, name: str, members_raw: list[dict], method: str,
                     config: dict | None, model: dict | None, metrics: dict | None,
                     enabled: bool) -> dict:
    """创建因子组合的**单一真源**编排，连带建伴生 ``factor_score`` 策略（单事务），REST 与 Agent 共用。

    ``validate_members`` → ``normalize_thresholds`` → ``assert_norm_contract`` → ``assert_weights_cover``
    → ``add_factor_set``（flush 取 id）→ ``ensure_companion_strategy``（落伴生策略）→ **单次 commit**。
    任一步抛 ``HTTPException`` 时事务未提交，组合与策略均不落（原子回滚，P0）。归一化契约/权重单源校验
    仅当 ``model`` 带训练产物时生效（equal/未训练自动放行）。

    Returns:
        ``factor_set_out`` + ``strategyId``（伴生策略 id）。

    Raises:
        HTTPException: 400 —— 成员非法 / 归一化窗口漂移 / 训练权重陈旧（与 REST 同一错误码）。
    """
    members = validate_members(members_raw)
    thresholds = normalize_thresholds((config or {}).get("thresholds"))
    assert_norm_contract(thresholds, model)
    assert_weights_cover(members, model)
    values = repo.FactorSetValues(name=name, members=members, method=method, config=config,
                                  model=model, metrics=metrics, enabled=enabled)
    row = await repo.add_factor_set(session, values=values)
    companion = await ensure_companion_strategy(session, row)
    await session.commit()
    await session.refresh(row)
    return _set_out_with_strategy(row, companion.id)


async def update_set(session: AsyncSession, set_id: int, *, name: str | None, members_raw: list[dict] | None,
                     method: str | None, config: dict | None, model: dict | None,
                     metrics: dict | None, enabled: bool | None) -> dict | None:
    """更新组合并同步刷新 / 自愈伴生策略（单事务）；组合不存在 → ``None``（路由转 404）。

    校验顺序与原 ``api.update_factor_set`` 一致（partial：仅在请求带对应字段时校验）：校验 →
    ``apply_factor_set_patch``（flush）→ ``ensure_companion_strategy``（坍缩到正典 + 刷新 / 缺失则补建）
    → **单次 commit**。``enabled`` 字段对组合按 patch 生效，但伴生策略的 ``enabled`` 不被覆盖（R9）。

    Raises:
        HTTPException: 400 —— 成员非法 / 归一化窗口漂移 / 训练权重陈旧。
    """
    members = validate_members(members_raw) if members_raw is not None else None
    if model is not None:
        if config is not None:
            assert_norm_contract(normalize_thresholds((config or {}).get("thresholds")), model)
        if members is not None:
            assert_weights_cover(members, model)
    row = await repo.get_factor_set(session, set_id)
    if row is None:
        return None
    patch = repo.FactorSetPatch(name=name, members=members, method=method, config=config,
                                model=model, metrics=metrics, enabled=enabled)
    await repo.apply_factor_set_patch(session, row, patch=patch)
    companion = await ensure_companion_strategy(session, row)
    await session.commit()
    await session.refresh(row)
    return _set_out_with_strategy(row, companion.id)


async def delete_set(session: AsyncSession, set_id: int) -> str:
    """删除组合并**连带删除其全部伴生 ``factor_score`` 策略**（单事务）；'ok' | 'not_found'。

    有意反转旧「删组合不影响策略」不变量（R3，用户明确要求 1:1 生命周期）：按 ``factorSetId`` 反查全部
    伴生 → 逐条 ``session.delete`` → 删组合行 → **单次 commit**。本机 SQLite 未开外键，删带 run 的
    ``factor_score`` 行与 ``repo.delete_strategy``（交易面板删用户策略）同口径、不因 ``strategy_runs``
    外键失败；已发起的 run 自包含、不受影响（历史经 ``strategy_id`` 仍可查阅）。
    """
    row = await repo.get_factor_set(session, set_id)
    if row is None:
        return "not_found"
    for strategy_row in await repo.list_factor_score_rows_for_set(session, set_id):
        await session.delete(strategy_row)
    await session.delete(row)
    await session.commit()
    return "ok"


async def backfill_companions(session: AsyncSession) -> int:
    """对历史**无伴生**的组合幂等补建一条伴生 ``factor_score`` 策略；返回补建条数（启动 lifespan 调用）。

    遍历全部组合，仅对 ``list_factor_score_rows_for_set`` 为空者 ``ensure_companion_strategy`` 补建
    （已有伴生即跳过，不重复建、不改动）。补建后**单次 commit**；幂等、可重复运行。
    """
    created = 0
    for set_row in await repo.list_factor_sets(session):
        if not await repo.list_factor_score_rows_for_set(session, set_row.id):
            await ensure_companion_strategy(session, set_row)
            created += 1
    if created:
        await session.commit()
    return created


def resolve_set_to_spec(row: FactorSet) -> dict:
    """因子组合行 → ``{members, thresholds, normStats, provenance}`` 自包含快照（创建 factor_score 策略时内联，C-P1-2/H2）。

    ``provenance`` 为单源 / 前瞻性补全（仅供 UI 展示训练区间，绝不参与打分）；当前唯一建策略入口是前端
    createStrategy 内联快照，本解析器供未来「从已保存组合直接建策略」的服务端路径复用（评审 P2-2/L4）。
    """
    return {"members": members_from_set_row(row), "thresholds": thresholds_from_set_row(row),
            "normStats": norm_stats_from_set_row(row), "provenance": provenance_from_set_row(row)}


def _score_payload(df: pd.DataFrame, points: list[dict], norm_window: int) -> dict:
    """组装打分响应；无任何有效得分点时附 ``window_too_short`` 提示（区间过短可观测，H6）。"""
    latest = points[-1]["score"] if points else None
    coverage = round(len(points) / len(df), 4) if len(df) else 0.0
    out = {"points": points, "latest": latest, "coverage": coverage}
    if not points:
        # score 端点暖机≈滚动窗口 normWindow（min_periods=window）；不接收 horizons（评审 R-P2-2）。
        out["note"] = "window_too_short"
        out["minBarsRequired"] = int(norm_window)
    return out


async def score_set(rest, window: FactorWindow, row: FactorSet) -> dict:
    """对保存的因子组合算复合得分序列：``{points:[{ts,score}],latest,coverage[,note,minBarsRequired]}``。"""
    spec = resolve_set_to_spec(row)
    thresholds = spec["thresholds"]
    norm_window, clip = resolve_norm_contract(norm_stats=spec.get("normStats"), thresholds=thresholds)
    df = await _load_df(rest, window)
    score = await run_in_threadpool(
        compose_score, df, spec["members"], None,
        norm_window=norm_window, clip=clip,
        squash=bool(thresholds.get("squash", True)), bar=window.bar)
    payload = _score_payload(df, score_points(df, score), norm_window)
    # 组合任一成员对当前 bar 不兼容 → bar_incompatible 优先于 window_too_short（L5）。
    if _members_bar_incompatible(spec["members"], window.bar):
        payload.pop("minBarsRequired", None)
        payload["note"] = "bar_incompatible"
    return payload


async def score_inline(rest, window: FactorWindow, *, members: list[dict],
                       thresholds: dict | None) -> dict:
    """对临时（未保存）内联组合算复合得分序列（FactorSetBuilder 预览用）。

    未保存组合无 normStats 来源，``resolve_norm_contract`` 走 thresholds 回落分支即可（评审 C-2）。
    """
    members = validate_members(members)
    resolved = normalize_thresholds(thresholds)
    norm_window, clip = resolve_norm_contract(norm_stats=None, thresholds=resolved)
    df = await _load_df(rest, window)
    score = await run_in_threadpool(
        compose_score, df, members, None,
        norm_window=norm_window, clip=clip,
        squash=bool(resolved.get("squash", True)), bar=window.bar)
    payload = _score_payload(df, score_points(df, score), norm_window)
    # 组合任一成员对当前 bar 不兼容 → bar_incompatible 优先于 window_too_short（L5）。
    if _members_bar_incompatible(members, window.bar):
        payload.pop("minBarsRequired", None)
        payload["note"] = "bar_incompatible"
    return payload
