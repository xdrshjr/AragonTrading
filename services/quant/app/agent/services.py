"""可复用回测 / 对比 helper（从 api/strategies.py 抽取，UI 与 Agent 共用）(spec §3.1)。

为避免与现有测试的 monkeypatch 目标漂移，OKX 客户端经参数 `rest` 注入（调用方在各自
命名空间内解析 `get_rest_client`），故行为与原 router 逐字节一致（R8 等价性回归护栏）。
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import repositories as repo
from ..db.models import Strategy, StrategyRun
from ..engine.backtester import BacktestResult, plan_walk_forward_windows, run_backtest
from ..engine.combo import ComboMember, ComboPolicy, run_combo_backtest
from ..engine.metrics import summarize_windows
from ..engine.mtf import bar_to_seconds
from ..okx.types import Candle
from ..schemas import combo_out, equity_out
from ..strategies.base import BaseStrategy, get_strategy_class
from ..strategies.combo_specs import (
    build_members as _build_members,
    combo_spec_from_row as _combo_spec_from_row,
    is_combo as _is_combo,
    policy_from_spec as _policy_from_spec,
    validate_combo_params,
)


# ============================ 取数（边界处显式错误，C1） ============================
_PAGE_CHUNK = 1000   # 单次 get_candles 上限（其内部 history-candles 翻页累积到该数，rest_client.py:160）
_COMPARE_SYNC_WORK_UNIT_LIMIT = 150_000
_COMPARE_EQUITY_MAX_POINTS = 1000


def _filter_range_candles(candles: list[Candle], start_ts: int, end_ts: int) -> list[Candle]:
    return [c for c in candles if start_ts <= c.ts <= end_ts and getattr(c, "confirm", True)]


async def _page_candles_back(rest, *, inst_id: str, bar: str, anchor_ts: int,
                             target: int, oldest_stop: int) -> dict[int, Candle]:
    """从 anchor_ts 向更早方向反复翻页累积去重（全区间分页核心，spec §3.2）。

    复用 `rest.get_candles(before=cursor)`（OKX 游标语义已在其内部处理），外层按 cursor 向更早推进，
    直到：覆盖到 `oldest_stop` / 达到 `target` 根 / 空页 / 游标不再前进（`oldest >= cursor`，
    含 stub 固定返回同一批次的情形 → 一轮即终止，防死循环）。

    Args:
        anchor_ts: 起始游标（取**早于**该时刻的更早历史；须 > 区间右界）。
        target: 安全根数上限（已与 max_bars 夹取），累积达到即停。
        oldest_stop: 左界停止线（base 取 start_ts；MTF 取 start_ts - warmup·htf_sec 以覆盖暖机段）。

    Returns:
        `{ts: Candle}`（未排序、未过滤）；过滤与排序由调用方按各自口径处理。
    """
    collected: dict[int, Candle] = {}
    cursor = anchor_ts
    pages_cap = max(1, target // _PAGE_CHUNK + 3)
    for _ in range(pages_cap):
        batch = await rest.get_candles(inst_id, bar, _PAGE_CHUNK, before=cursor)
        if not batch:
            break
        for c in batch:
            collected[c.ts] = c
        oldest = min(c.ts for c in batch)
        if oldest <= oldest_stop or oldest >= cursor:   # 已覆盖左界 / 游标不前进 → 停
            break
        cursor = oldest
        if len(collected) >= target:
            break
    return collected


async def load_range_candles(rest, *, inst_id: str, bar: str, start_ts: int, end_ts: int,
                             max_bars: int | None = None) -> list[Candle]:
    """完整区间分页拉取：保证 `[start_ts, end_ts]` 全覆盖（受 max_bars 安全上限约束，spec §3.2）。

    以 `end_ts` 为锚向更早反复翻页（复用 `rest.get_candles` 的 OKX 游标语义），去重后按
    `[start_ts, end_ts]` 过滤并升序。绝不回退假数据：区间内无已确认 K 线 → 400 EMPTY_RANGE（C1）。

    Args:
        max_bars: 取数根数安全上限；缺省取 `settings.backtest_max_bars`，显式给值仍被其二次夹取。

    Returns:
        升序、已确认、落在 `[start_ts, end_ts]` 内的 Candle 列表。

    Raises:
        HTTPException: 400 EMPTY_RANGE —— 区间内无已确认 K 线。
    """
    bar_sec = bar_to_seconds(bar)
    ceiling = get_settings().backtest_max_bars
    if max_bars is not None:
        ceiling = min(int(max_bars), ceiling)
    expected = (end_ts - start_ts) // bar_sec + 1
    target = min(int(expected), ceiling)
    collected = await _page_candles_back(rest, inst_id=inst_id, bar=bar, anchor_ts=end_ts + bar_sec,
                                         target=target, oldest_stop=start_ts)
    filtered = _filter_range_candles(list(collected.values()), start_ts, end_ts)
    filtered.sort(key=lambda c: c.ts)
    if not filtered:
        raise HTTPException(400, detail={"code": "EMPTY_RANGE",
                                         "message": "No confirmed candles in requested range"})
    return filtered


async def load_htf_frames(rest, *, inst_id: str, start_ts: int, end_ts: int, base_seconds: int,
                          timeframes: list[str], htf_warmup_bars: int) -> dict[str, list[Candle]]:
    """对每个严格高于基础周期的 bar 拉真实 K 线（全区间覆盖 + start_ts 前暖机），过滤未确认与越界。

    （v2·P1-4）以 `end_ts` 为锚向更早回溯，左界停在 `start_ts - htf_warmup_bars·htf_sec`，确保最靠前
    窗口在 `start_ts` 处能取到「已收盘」的高周期值（`merge_asof(backward)` 非 NaN）。高周期根数远少于
    基础周期，但长区间仍可能 > 1000，故同走全区间分页；根数受 `settings.backtest_max_bars` 收口。
    """
    ceiling = get_settings().backtest_max_bars
    frames: dict[str, list[Candle]] = {}
    for tf in timeframes:
        htf_seconds = bar_to_seconds(tf)
        if htf_seconds <= base_seconds:
            continue
        span_bars = (end_ts - start_ts) // htf_seconds + 1
        target = min(int(span_bars) + int(htf_warmup_bars), ceiling)
        oldest_stop = start_ts - int(htf_warmup_bars) * htf_seconds   # 保留 start_ts 前暖机覆盖
        collected = await _page_candles_back(rest, inst_id=inst_id, bar=tf, anchor_ts=end_ts + 1,
                                             target=target, oldest_stop=oldest_stop)
        cs = [c for c in collected.values() if c.ts <= end_ts and getattr(c, "confirm", True)]
        cs.sort(key=lambda c: c.ts)
        frames[tf] = cs
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
                               inst_id: str, bar: str, limit: int | None, start_ts: int, end_ts: int,
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


def _coverage_fields(candles: list[Candle], *, bar: str, start_ts: int, end_ts: int) -> dict:
    """从已取 candles 派生「全区间覆盖」透明度字段（纯计算，无 IO，spec §3.2 / P1-1）。

    truncated：最早一根仍晚于左界（未覆盖到 start_ts，含撞 max_bars 上限 / OKX 历史不足两种情形）。
    """
    bar_sec = bar_to_seconds(bar)
    requested_bars = (end_ts - start_ts) // bar_sec + 1
    truncated = bool(candles[0].ts > start_ts + bar_sec) if candles else True
    return {"effectiveCandleCount": len(candles), "requestedBars": int(requested_bars),
            "truncated": truncated}


async def run_single_backtest(session: AsyncSession, rest, *, strategy_id: int, inst_id: str,
                              bar: str, limit: int | None = None, start_ts: int, end_ts: int,
                              initial_capital: float | None = None, fees: float | None = None,
                              slippage: float | None = None, leverage: float | None = None,
                              params: dict | None = None) -> dict:
    """单策略回测：返回 {runId, metrics, effectiveCandleCount, requestedBars, truncated}（v2·P1-1）。

    全区间分页保证 `[startTs, endTs]` 全覆盖；覆盖情况经响应字段透明回传（前端据 `truncated` 显示横幅，
    绝不静默截断）。`limit` 若给出仅作 `max_bars` 安全上限覆盖（不再当覆盖上限）。若该策略是组合
    （kind=="combo"），内部转 combo 路径，响应在 {runId, metrics} 基础上**追加** `combo` 字段
    （combo 路径不强制回包覆盖字段，避免响应形状漂移，§3.2）。组合仅用已存 params_json，不透传 `params`（R7）。
    """
    row = await repo.get_strategy_row(session, strategy_id)
    if row is not None and _is_combo(row.template_key):
        return await _run_saved_combo(
            session, rest, row, inst_id=inst_id, bar=bar, limit=limit,
            start_ts=start_ts, end_ts=end_ts, initial_capital=initial_capital, fees=fees,
            slippage=slippage, leverage=leverage)
    _row, strategy = await _prepare_strategy(session, strategy_id, params)
    costs = _resolve_costs(get_settings(), initial_capital, fees, slippage, leverage)
    candles = await load_range_candles(rest, inst_id=inst_id, bar=bar, start_ts=start_ts,
                                       end_ts=end_ts, max_bars=limit)
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
    return {"runId": run.id, "metrics": result.metrics,
            **_coverage_fields(candles, bar=bar, start_ts=start_ts, end_ts=end_ts)}


def _dedupe_ids(ids: list[int]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for sid in ids:
        if sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


@dataclass(frozen=True)
class CompareComboItem:
    strategy_id: int
    row: Strategy
    members_spec: list[dict]
    policy_spec: dict
    members: list[ComboMember]
    policy: ComboPolicy


async def _prepare_compare(session: AsyncSession, ordered_ids: list[int]) -> list[tuple]:
    prepared: list[tuple] = []
    for sid in ordered_ids:
        row = await repo.get_strategy_row(session, sid)
        if row is None:
            continue
        if _is_combo(row.template_key):
            continue   # 组合走专属 combo 路径（不进原子撮合，避免 on_candle 抛错）
        cls = get_strategy_class(row.template_key)
        if cls is None:
            continue
        try:
            params = {**cls.meta.default_params, **json.loads(row.params_json or "{}")}
        except json.JSONDecodeError:
            params = dict(cls.meta.default_params)
        prepared.append((sid, row, cls(params)))
    return prepared


async def _prepare_compare_combos(
    session: AsyncSession,
    ordered_ids: list[int],
) -> list[CompareComboItem]:
    prepared: list[CompareComboItem] = []
    for sid in ordered_ids:
        row = await repo.get_strategy_row(session, sid)
        if row is None or not _is_combo(row.template_key):
            continue
        notes: list[str] = []
        try:
            members_spec, policy_spec = _combo_spec_from_row(row)
            members = _build_members(members_spec, notes)
        except HTTPException:
            continue
        prepared.append(CompareComboItem(
            strategy_id=sid,
            row=row,
            members_spec=members_spec,
            policy_spec=policy_spec,
            members=members,
            policy=_policy_from_spec(policy_spec),
        ))
    return prepared


def _compare_effective_bars(*, bar: str, limit: int | None, start_ts: int, end_ts: int) -> int:
    settings = get_settings()
    requested = (end_ts - start_ts) // bar_to_seconds(bar) + 1
    ceiling = settings.backtest_max_bars
    if limit is not None:
        ceiling = min(int(limit), ceiling)
    return max(0, min(int(requested), int(ceiling)))


def _guard_compare_workload(
    prepared: list[tuple],
    combos: list[CompareComboItem],
    *,
    bar: str,
    limit: int | None,
    start_ts: int,
    end_ts: int,
) -> None:
    expanded_count = len(prepared) + sum(len(combo.members) for combo in combos)
    if expanded_count <= 0:
        return
    effective_bars = _compare_effective_bars(
        bar=bar,
        limit=limit,
        start_ts=start_ts,
        end_ts=end_ts,
    )
    if expanded_count * effective_bars <= _COMPARE_SYNC_WORK_UNIT_LIMIT:
        return
    raise HTTPException(413, detail={
        "code": "COMPARE_TOO_LARGE",
        "message": (
            "Compare request is too large for synchronous execution. "
            "Narrow the range or select fewer combo strategies."
        ),
    })


def _member_htf_from_union(
    members: list[ComboMember],
    union_frames: dict[str, list[Candle]],
) -> dict[str, dict[str, list[Candle]]]:
    return {
        member.key: {
            tf: union_frames[tf]
            for tf in member.timeframes
            if tf in union_frames
        }
        for member in members
    }


def _downsample_compare_equity(points: list) -> list:
    if len(points) <= _COMPARE_EQUITY_MAX_POINTS:
        return points
    last_index = len(points) - 1
    selected = []
    previous_index = -1
    for slot in range(_COMPARE_EQUITY_MAX_POINTS):
        index = round(slot * last_index / (_COMPARE_EQUITY_MAX_POINTS - 1))
        if index != previous_index:
            selected.append(points[index])
            previous_index = index
    return selected


def _compare_equity_out(points: list) -> list[dict]:
    return [equity_out(e) for e in _downsample_compare_equity(points)]


async def run_compare(session: AsyncSession, rest, *, strategy_ids: list[int], inst_id: str,
                      bar: str, limit: int | None = None, start_ts: int, end_ts: int,
                      initial_capital: float | None = None, fees: float | None = None,
                      slippage: float | None = None, leverage: float | None = None,
                      cancel_check: Callable[[], bool] | None = None) -> dict:
    """多策略横向对比：候选 candles 与 HTF 并集各取一次，逐策略回测并各自落库（F3 / P1-2）。

    Args:
        cancel_check: 可选协作式取消探针（H11）；逐策略前命中即提前返回已完成项，余者跳过。
            UI router 不传（None → 不检查 → 行为与原路由逐字节一致，R8）。
    """
    ordered_ids = _dedupe_ids(strategy_ids)
    costs = _resolve_costs(get_settings(), initial_capital, fees, slippage, leverage)
    prepared = await _prepare_compare(session, ordered_ids)
    prepared_combos = await _prepare_compare_combos(session, ordered_ids)
    _guard_compare_workload(
        prepared,
        prepared_combos,
        bar=bar,
        limit=limit,
        start_ts=start_ts,
        end_ts=end_ts,
    )
    candles = await load_range_candles(rest, inst_id=inst_id, bar=bar, start_ts=start_ts,
                                       end_ts=end_ts, max_bars=limit)
    await repo.upsert_candles(session, inst_id, bar, candles)

    base_seconds = bar_to_seconds(bar)
    atomic_tfs = {tf for _, _, s in prepared for tf in s.timeframes()
                  if tf != bar and bar_to_seconds(tf) > base_seconds}
    combo_tfs = {tf for combo in prepared_combos for member in combo.members
                 for tf in member.timeframes
                 if tf != bar and bar_to_seconds(tf) > base_seconds}
    union_tfs = sorted(atomic_tfs | combo_tfs)
    atomic_warmups = [s.htf_warmup_bars() for _, _, s in prepared]
    combo_warmups = [
        member.strategy.htf_warmup_bars()
        for combo in prepared_combos
        for member in combo.members
    ]
    max_warmup = max(atomic_warmups + combo_warmups, default=60)
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
            "equity": _compare_equity_out(result.equity_points),
        })

    # 组合项：复用共享 base candles 和共享 HTF 并集；与原子项同构可混排对比。
    for combo in prepared_combos:
        if cancel_check is not None and cancel_check():
            break
        htf_by_member = _member_htf_from_union(combo.members, union_frames)
        item = await _compare_combo_item(
            session, rest, combo, candles, inst_id=inst_id, bar=bar, limit=limit,
            start_ts=start_ts, end_ts=end_ts, costs=costs, htf_by_member=htf_by_member)
        if item is not None:
            items.append(item)
    return {"instId": inst_id, "bar": bar, "items": items}


# ============================ 组合编排（regime rotation） ============================
async def _combo_template_strategy_id(session: AsyncSession) -> int:
    """已 seed 的 regime_rotation builtin 行 id（预览 run 的 strategy_id 落点，R1）。

    init_db → _seed_builtin_strategies 必然种入且 builtin 不可删，行恒在；意外缺失 → 显式 500。
    """
    stmt = (select(Strategy.id)
            .where(Strategy.template_key == "regime_rotation", Strategy.builtin == True)  # noqa: E712
            .limit(1))
    sid = (await session.execute(stmt)).scalars().first()
    if sid is None:
        raise HTTPException(500, detail={"code": "COMBO_TEMPLATE_MISSING",
                                         "message": "组合模板未初始化"})
    return sid


async def _load_member_htf(session: AsyncSession, rest, members: list[ComboMember], *,
                           inst_id: str, bar: str, start_ts: int, end_ts: int) -> dict:
    """成员 timeframes 并集取真实高周期帧，按成员归集 {memberKey: {htf_bar: candles}}。"""
    base_seconds = bar_to_seconds(bar)
    union_tfs = sorted({tf for m in members for tf in m.timeframes
                        if tf != bar and bar_to_seconds(tf) > base_seconds})
    union_frames: dict[str, list[Candle]] = {}
    if union_tfs:
        max_warmup = max((m.strategy.htf_warmup_bars() for m in members), default=60)
        union_frames = await load_htf_frames(rest, inst_id=inst_id, start_ts=start_ts, end_ts=end_ts,
                                             base_seconds=base_seconds, timeframes=union_tfs,
                                             htf_warmup_bars=max_warmup)
        for tf, cs in union_frames.items():
            await repo.upsert_candles(session, inst_id, tf, cs)
    return {m.key: {tf: union_frames[tf] for tf in m.timeframes if tf in union_frames}
            for m in members}


async def _run_combo(session: AsyncSession, rest, *, strategy_id: int, members: list[ComboMember],
                     policy: ComboPolicy, inst_id: str, bar: str, limit: int | None, start_ts: int,
                     end_ts: int, costs: tuple, config_extra: dict,
                     candles: list[Candle] | None = None,
                     htf_by_member: dict | None = None):
    """加载成员高周期（必要时含基础 K 线）→ 线程池组合回测 → 落库。返回 (run, result)。"""
    initial_capital, fees, slippage, leverage = costs
    if candles is None:
        candles = await load_range_candles(rest, inst_id=inst_id, bar=bar, start_ts=start_ts,
                                           end_ts=end_ts, max_bars=limit)
        await repo.upsert_candles(session, inst_id, bar, candles)
    if htf_by_member is None:
        htf_by_member = await _load_member_htf(session, rest, members, inst_id=inst_id, bar=bar,
                                               start_ts=start_ts, end_ts=end_ts)
    run = await repo.create_run(
        session, strategy_id=strategy_id, inst_id=inst_id, bar=bar, mode="backtest",
        config={"combo": True, **config_extra, "initialCapital": initial_capital, "fees": fees,
                "slippage": slippage, "leverage": leverage, "limit": limit, "startTs": start_ts,
                "endTs": end_ts, "effectiveCandleCount": len(candles)})
    result = await run_in_threadpool(
        run_combo_backtest, members, candles, bar, initial_capital=initial_capital,
        taker_fee_bps=fees, slippage_bps=slippage, leverage=leverage,
        htf_frames_by_member=htf_by_member, policy=policy)
    await repo.persist_backtest_result(session, run, trades=result.trades,
                                       equity=result.equity_points, metrics=result.metrics,
                                       start_ts=result.start_ts, end_ts=result.end_ts)
    return run, result


async def run_combo_preview(session: AsyncSession, rest, *, members_spec: list[dict],
                            policy_spec: dict | None, inst_id: str, bar: str, limit: int | None = None,
                            start_ts: int, end_ts: int, initial_capital: float | None = None,
                            fees: float | None = None, slippage: float | None = None,
                            leverage: float | None = None) -> dict:
    """预览（未保存）组合：strategy_id 落已 seed 的 regime_rotation builtin 行（R1）。"""
    notes: list[str] = []
    members = _build_members(members_spec, notes)
    policy = _policy_from_spec(policy_spec)
    costs = _resolve_costs(get_settings(), initial_capital, fees, slippage, leverage)
    sid = await _combo_template_strategy_id(session)
    run, result = await _run_combo(
        session, rest, strategy_id=sid, members=members, policy=policy, inst_id=inst_id, bar=bar,
        limit=limit, start_ts=start_ts, end_ts=end_ts, costs=costs,
        config_extra={"preview": True, "members": members_spec, "policy": policy_spec or {}})
    combo_notes = list(dict.fromkeys(notes + list(result.notes)))
    return {"runId": run.id, "metrics": result.metrics, "combo": combo_out(result, combo_notes)}


async def _run_saved_combo(session: AsyncSession, rest, row: Strategy, *, inst_id: str, bar: str,
                           limit: int | None, start_ts: int, end_ts: int,
                           initial_capital: float | None = None, fees: float | None = None,
                           slippage: float | None = None, leverage: float | None = None) -> dict:
    """保存组合（有自身 id）走 combo 路径，strategy_id 即其本身。"""
    notes: list[str] = []
    members_spec, policy_spec = _combo_spec_from_row(row)
    members = _build_members(members_spec, notes)
    policy = _policy_from_spec(policy_spec)
    costs = _resolve_costs(get_settings(), initial_capital, fees, slippage, leverage)
    run, result = await _run_combo(
        session, rest, strategy_id=row.id, members=members, policy=policy, inst_id=inst_id, bar=bar,
        limit=limit, start_ts=start_ts, end_ts=end_ts, costs=costs,
        config_extra={"preview": False, "members": members_spec, "policy": policy_spec})
    combo_notes = list(dict.fromkeys(notes + list(result.notes)))
    return {"runId": run.id, "metrics": result.metrics, "combo": combo_out(result, combo_notes)}


async def _compare_combo_item(session: AsyncSession, rest, combo: CompareComboItem,
                              candles: list[Candle], *,
                              inst_id: str, bar: str, limit: int | None, start_ts: int, end_ts: int,
                              costs: tuple, htf_by_member: dict) -> dict | None:
    """compare 中的组合项：复用共享 base candles 和共享 HTF frames。"""
    run, result = await _run_combo(
        session, rest, strategy_id=combo.row.id, members=combo.members, policy=combo.policy,
        inst_id=inst_id, bar=bar,
        limit=limit, start_ts=start_ts, end_ts=end_ts, costs=costs, candles=candles,
        htf_by_member=htf_by_member,
        config_extra={
            "preview": False,
            "compare": True,
            "members": combo.members_spec,
            "policy": combo.policy_spec,
        })
    return {"strategyId": combo.row.id, "name": combo.row.name or "组合", "runId": run.id,
            "metrics": result.metrics, "equity": _compare_equity_out(result.equity_points)}


# ============================ 高级回测：滑动窗口 / Walk-forward（spec §3.3） ============================
@dataclass(frozen=True)
class WalkForwardRunContext:
    inst_id: str
    bar: str
    start_ts: int
    end_ts: int
    window_bars: int
    step_bars: int
    reset_capital: bool
    initial_capital: float | None = None
    fees: float | None = None
    slippage: float | None = None
    leverage: float | None = None
    max_bars: int | None = None


@dataclass(frozen=True)
class ComboWalkForwardWindowContext:
    members_spec: list[dict]
    policy_spec: dict
    candles: list[Candle]
    plan: list[tuple[int, int]]
    run_ctx: WalkForwardRunContext
    costs: tuple[float, float, float, float]
    htf_by_member: dict
    warmup: int


async def _resolve_strategy_spec(session: AsyncSession, strategy_id: int) -> tuple:
    """取原子策略行 + 注册表类 + 合并后 params。

    Returns:
        (row, cls, params)：DB 行、策略类、叠加默认后的实例参数 dict。

    Raises:
        HTTPException: 404 NOT_FOUND（策略不存在）/ 400 UNAVAILABLE（模板已失效）。
    """
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
    return row, cls, params


async def _run_wf_windows(cls, params: dict, candles: list[Candle], bar: str,
                          plan: list[tuple[int, int]], *, reset_capital: bool, icap: float,
                          fee: float, slip: float, lev: float,
                          htf_frames: dict[str, list[Candle]], warmup: int) -> tuple[list[dict], list[str]]:
    """逐窗口跑回测并收集窗口结果与提示 notes（纯编排，无 IO，spec §3.3）。

    每个窗口用全新策略实例 `cls(params)`，杜绝跨窗口状态泄漏（鲁棒性独立性的硬保证）。
    `reset_capital=False` 时上一窗口期末权益链式滚动为下一窗口起始本金。
    """
    windows: list[dict] = []
    notes: list[str] = []
    rolling = icap
    short_flagged = False
    for (i0, i1) in plan:
        sub = candles[i0:i1]
        cap = icap if reset_capital else rolling
        result = await run_in_threadpool(
            run_backtest, cls(params), sub, bar, initial_capital=cap, taker_fee_bps=fee,
            slippage_bps=slip, leverage=lev, htf_frames=htf_frames)
        if not reset_capital and result.equity_points:
            rolling = result.equity_points[-1].equity
        if not short_flagged and len(sub) < warmup + 5:   # P1-3：窗口偏短，暖机吃满 → 提示一次
            notes.append("部分窗口长度接近策略暖机根数，指标可能偏弱，建议增大 windowBars")
            short_flagged = True
        windows.append({"startTs": int(sub[0].ts), "endTs": int(sub[-1].ts),
                        "metrics": result.metrics, "tradeCount": result.metrics["tradeCount"]})
    return windows, notes


def _combo_warmup_estimate(members: list[ComboMember], policy: ComboPolicy) -> int:
    from ..engine.regime import RegimeParams
    regime_params = RegimeParams()
    regime_start = max(regime_params.ema_slow_p, regime_params.trend_win)
    member_warmup = max((member.strategy.warmup() for member in members), default=0)
    return max(member_warmup, regime_start, policy.lookback_bars)


def _slice_member_htf_for_window(htf_by_member: dict, start_ts: int, end_ts: int) -> dict:
    """窗口 HTF 只截掉未来数据；保留 start_ts 前历史用于指标暖机。"""
    _ = start_ts
    return {
        member_key: {
            tf: [candle for candle in candles if int(candle.ts) <= end_ts]
            for tf, candles in frames.items()
        }
        for member_key, frames in htf_by_member.items()
    }


async def _run_combo_wf_windows(
    window_ctx: ComboWalkForwardWindowContext,
) -> tuple[list[dict], list[str]]:
    windows: list[dict] = []
    notes: list[str] = []
    icap, fee, slip, lev = window_ctx.costs
    rolling = icap
    short_flagged = False
    for i0, i1 in window_ctx.plan:
        sub = window_ctx.candles[i0:i1]
        cap = icap if window_ctx.run_ctx.reset_capital else rolling
        window_htf = _slice_member_htf_for_window(
            window_ctx.htf_by_member,
            int(sub[0].ts),
            int(sub[-1].ts),
        )
        window_notes: list[str] = []
        members = _build_members(window_ctx.members_spec, window_notes)
        policy = _policy_from_spec(window_ctx.policy_spec)
        result = await run_in_threadpool(
            run_combo_backtest,
            members,
            sub,
            window_ctx.run_ctx.bar,
            initial_capital=cap,
            taker_fee_bps=fee,
            slippage_bps=slip,
            leverage=lev,
            htf_frames_by_member=window_htf,
            policy=policy,
        )
        notes.extend(window_notes)
        notes.extend(list(result.notes))
        if not window_ctx.run_ctx.reset_capital and result.equity_points:
            rolling = result.equity_points[-1].equity
        if not short_flagged and len(sub) < window_ctx.warmup + 5:
            notes.append("部分窗口长度接近组合暖机根数，指标可能偏弱，建议增大 windowBars")
            short_flagged = True
        windows.append({
            "startTs": int(sub[0].ts),
            "endTs": int(sub[-1].ts),
            "metrics": result.metrics,
            "tradeCount": result.metrics["tradeCount"],
        })
    return windows, notes


async def _run_atomic_walk_forward(
    session: AsyncSession,
    rest,
    row: Strategy,
    ctx: WalkForwardRunContext,
) -> dict:
    _row, cls, params = await _resolve_strategy_spec(session, row.id)
    meta_strategy = cls(params)                                          # P2-6：元信息单实例复用
    icap, fee, slip, lev = _resolve_costs(
        get_settings(),
        ctx.initial_capital,
        ctx.fees,
        ctx.slippage,
        ctx.leverage,
    )
    candles = await load_range_candles(
        rest,
        inst_id=ctx.inst_id,
        bar=ctx.bar,
        start_ts=ctx.start_ts,
        end_ts=ctx.end_ts,
        max_bars=ctx.max_bars,
    )
    await repo.upsert_candles(session, ctx.inst_id, ctx.bar, candles)
    warmup = max(meta_strategy.warmup(), 1)
    htf_frames: dict[str, list[Candle]] = {}
    tfs = [tf for tf in meta_strategy.timeframes() if tf != ctx.bar]
    if tfs:   # P1-4：load_htf_frames 保留 start_ts 之前 htf_warmup_bars 覆盖
        htf_frames = await load_htf_frames(
            rest,
            inst_id=ctx.inst_id,
            start_ts=ctx.start_ts,
            end_ts=ctx.end_ts,
            base_seconds=bar_to_seconds(ctx.bar),
            timeframes=tfs,
            htf_warmup_bars=meta_strategy.htf_warmup_bars(),
        )
        for tf, cs in htf_frames.items():
            await repo.upsert_candles(session, ctx.inst_id, tf, cs)

    plan = plan_walk_forward_windows(len(candles), ctx.window_bars, ctx.step_bars)
    if not plan:
        raise HTTPException(400, detail={"code": "WF_RANGE_TOO_SHORT",
                                         "message": "区间内无足够 K 线生成窗口"})
    if len(plan) > get_settings().walk_forward_max_windows:
        raise HTTPException(400, detail={"code": "WF_TOO_MANY_WINDOWS",
                                         "message": "窗口数过多，请增大步长或缩短区间"})

    parent = await repo.create_run(
        session, strategy_id=row.id, inst_id=ctx.inst_id, bar=ctx.bar, mode="walkforward",
        config={"kind": "walkforward", "windowBars": ctx.window_bars,
                "stepBars": ctx.step_bars, "resetCapital": ctx.reset_capital,
                "initialCapital": icap, "fees": fee, "slippage": slip, "leverage": lev,
                "startTs": ctx.start_ts, "endTs": ctx.end_ts,
                "effectiveCandleCount": len(candles)})
    windows, notes = await _run_wf_windows(
        cls, params, candles, ctx.bar, plan, reset_capital=ctx.reset_capital, icap=icap,
        fee=fee, slip=slip, lev=lev, htf_frames=htf_frames, warmup=warmup)
    covered = plan[-1][1] if plan else 0
    uncovered = max(0, len(candles) - covered)
    if len(plan) == 1 and len(candles) < ctx.window_bars:
        notes.append("区间数据不足一个完整窗口，已退化为单窗口")
    summary = summarize_windows(windows, uncovered_bars=uncovered, notes=notes)
    await repo.persist_walk_forward_result(
        session,
        parent,
        summary=summary,
        windows=windows,
        start_ts=int(candles[0].ts),
        end_ts=int(candles[-1].ts),
    )
    return {"runId": parent.id, "summary": summary, "windows": windows}


async def _run_combo_walk_forward(
    session: AsyncSession,
    rest,
    row: Strategy,
    ctx: WalkForwardRunContext,
) -> dict:
    members_spec, policy_spec = _combo_spec_from_row(row)
    notes: list[str] = []
    validation_members = _build_members(members_spec, notes)
    policy = _policy_from_spec(policy_spec)
    costs = _resolve_costs(
        get_settings(),
        ctx.initial_capital,
        ctx.fees,
        ctx.slippage,
        ctx.leverage,
    )
    icap, fee, slip, lev = costs
    candles = await load_range_candles(
        rest,
        inst_id=ctx.inst_id,
        bar=ctx.bar,
        start_ts=ctx.start_ts,
        end_ts=ctx.end_ts,
        max_bars=ctx.max_bars,
    )
    await repo.upsert_candles(session, ctx.inst_id, ctx.bar, candles)
    htf_by_member = await _load_member_htf(
        session,
        rest,
        validation_members,
        inst_id=ctx.inst_id,
        bar=ctx.bar,
        start_ts=ctx.start_ts,
        end_ts=ctx.end_ts,
    )
    plan = plan_walk_forward_windows(len(candles), ctx.window_bars, ctx.step_bars)
    if not plan:
        raise HTTPException(400, detail={"code": "WF_RANGE_TOO_SHORT",
                                         "message": "区间内无足够 K 线生成窗口"})
    if len(plan) > get_settings().walk_forward_max_windows:
        raise HTTPException(400, detail={"code": "WF_TOO_MANY_WINDOWS",
                                         "message": "窗口数过多，请增大步长或缩短区间"})

    parent = await repo.create_run(
        session,
        strategy_id=row.id,
        inst_id=ctx.inst_id,
        bar=ctx.bar,
        mode="walkforward",
        config={
            "kind": "walkforward",
            "subjectKind": "combo",
            "combo": True,
            "members": members_spec,
            "policy": policy_spec,
            "windowBars": ctx.window_bars,
            "stepBars": ctx.step_bars,
            "resetCapital": ctx.reset_capital,
            "initialCapital": icap,
            "fees": fee,
            "slippage": slip,
            "leverage": lev,
            "startTs": ctx.start_ts,
            "endTs": ctx.end_ts,
            "effectiveCandleCount": len(candles),
        },
    )
    window_ctx = ComboWalkForwardWindowContext(
        members_spec=members_spec,
        policy_spec=policy_spec,
        candles=candles,
        plan=plan,
        run_ctx=ctx,
        costs=(icap, fee, slip, lev),
        htf_by_member=htf_by_member,
        warmup=_combo_warmup_estimate(validation_members, policy),
    )
    windows, window_notes = await _run_combo_wf_windows(window_ctx)
    covered = plan[-1][1] if plan else 0
    uncovered = max(0, len(candles) - covered)
    if len(plan) == 1 and len(candles) < ctx.window_bars:
        notes.append("区间数据不足一个完整窗口，已退化为单窗口")
    all_notes = list(dict.fromkeys(notes + window_notes))
    summary = summarize_windows(windows, uncovered_bars=uncovered, notes=all_notes)
    await repo.persist_walk_forward_result(
        session,
        parent,
        summary=summary,
        windows=windows,
        start_ts=int(candles[0].ts),
        end_ts=int(candles[-1].ts),
    )
    return {"runId": parent.id, "summary": summary, "windows": windows}


async def run_walk_forward(session: AsyncSession, rest, *, strategy_id: int, inst_id: str, bar: str,
                           start_ts: int, end_ts: int, window_bars: int, step_bars: int,
                           reset_capital: bool, initial_capital: float | None = None,
                           fees: float | None = None, slippage: float | None = None,
                           leverage: float | None = None, max_bars: int | None = None) -> dict:
    """滑动窗口高级回测编排：原子策略与保存后的组合策略共用同一路由。

    Returns:
        {runId, summary, windows}（与 POST /strategies/{id}/walk-forward 响应同构）。
    """
    row = await repo.get_strategy_row(session, strategy_id)
    if row is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "策略不存在"})
    ctx = WalkForwardRunContext(
        inst_id=inst_id,
        bar=bar,
        start_ts=start_ts,
        end_ts=end_ts,
        window_bars=window_bars,
        step_bars=step_bars,
        reset_capital=reset_capital,
        initial_capital=initial_capital,
        fees=fees,
        slippage=slippage,
        leverage=leverage,
        max_bars=max_bars,
    )
    if _is_combo(row.template_key):
        return await _run_combo_walk_forward(session, rest, row, ctx)
    return await _run_atomic_walk_forward(session, rest, row, ctx)
