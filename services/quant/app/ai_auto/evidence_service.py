"""实证准入的装配与 IO 层（ai-trade-evidence-gated-capital §3.6）。

四件事：启动快照（含可接管的未结束影子）、运行时待写影子的批量落库、向导预览接口的回执装配、
从历史成交回填证据。判据一律在纯核心（`shadow_ledger` / `evidence_gate`），这里只取数与写库。
"""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import ai_evidence_repositories as erepo
from ..db.models import StrategyRun, Trade
from ..db.session import get_sessionmaker
from ..engine.indicators import atr as atr_series
from .evidence_gate import (
    EvidenceRule,
    bucket_stats,
    gating_of,
    resolve_state,
    stats_wire,
)
from .evidence_runtime import POOLED_WINDOW_MULT, SIDES, EvidenceSnapshot
from .shadow_ledger import (
    STATUS_OPEN,
    Shadow,
    advance_shadow,
    bar_seconds,
    open_shadow,
)

__all__ = ["EvidenceSnapshot", "load_snapshots", "persist_rows", "evidence_view", "backfill",
           "row_values", "shadow_from_row", "EvidenceFetchError"]

logger = logging.getLogger(__name__)

BACKFILL_DEADLINE_S = 240.0     # 小于代理的 300 s；到点停下，唯一约束保证再点一次能接着做
RATE_WINDOW_DAYS = 7
_ATR_PERIOD = 14
_PRE_BARS = _ATR_PERIOD + 2     # 开仓前要有这么多根已收盘根才能算 ATR

FetchCandles = Callable[[str, str, int, int], Awaitable[list[dict]]]


class EvidenceFetchError(RuntimeError):
    """回填的公共读整体失败（端点转 502 `AI_TRADE_EVIDENCE_BACKFILL_FETCH_FAILED`）。"""


# ---------------- 行 ⇄ 影子 ----------------
def row_values(shadow: Shadow, *, source: str = erepo.SOURCE_LIVE,
               source_ref: str | None = None, group_id: str = "") -> dict:
    """影子 → 表列（实时行 `source_ref` 恒为 `shadow_key`，评审 P1-2）。"""
    return {
        "shadow_key": shadow.shadow_id, "source": source,
        "source_ref": source_ref if source_ref is not None else shadow.shadow_id,
        "inst_id": shadow.inst_id, "bar": shadow.bar, "side": shadow.side,
        "policy_version": shadow.policy_version, "run_id": shadow.run_id,
        "group_id": group_id or "", "round_no": shadow.round_no,
        "opened_wall": shadow.opened_wall, "opened_bar_ts": shadow.opened_bar_ts,
        "entry_px": shadow.entry_px, "cost_bps": shadow.cost_bps, "atr_bps": shadow.atr_bps,
        "sl_bps": shadow.sl_bps, "tp_bps": shadow.tp_bps, "traded": shadow.traded,
        "repeats": shadow.repeats, "bars_seen": shadow.bars_seen,
        "last_bar_ts": shadow.last_bar_ts, "mfe_bps": shadow.mfe_bps,
        "mae_bps": shadow.mae_bps, "status": shadow.status,
        "exit_kind": shadow.exit_kind or "", "exit_px": shadow.exit_px,
        "matured_wall": shadow.exit_wall if shadow.status == "matured" else None,
        "gross_bps": shadow.gross_bps, "net_bps": shadow.net_bps,
        "void_reason": shadow.void_reason or ""}


def shadow_from_row(row) -> Shadow:
    """表行 → 只含冻结入场参数的 open 影子（推进列是缓存，接管时从头重算，评审 P0-3）。"""
    return Shadow(
        shadow_id=row.shadow_key, inst_id=row.inst_id, bar=row.bar, side=row.side,
        opened_wall=float(row.opened_wall), entry_px=float(row.entry_px),
        cost_bps=float(row.cost_bps), atr_bps=float(row.atr_bps), sl_bps=float(row.sl_bps),
        tp_bps=float(row.tp_bps), policy_version=row.policy_version, run_id=int(row.run_id),
        round_no=int(row.round_no), opened_bar_ts=int(row.opened_bar_ts),
        traded=bool(row.traded), repeats=int(row.repeats or 0))


async def persist_rows(batch: list[Shadow], *, group_id: str = "", sessionmaker=None) -> None:
    """运行时待写影子的批量 upsert（失败原样上抛，由运行时计入 dropped）。"""
    factory = sessionmaker or get_sessionmaker
    async with factory()() as session:
        await erepo.upsert_shadows(session, [row_values(s, group_id=group_id) for s in batch])


# ---------------- 启动快照 ----------------
async def load_snapshots(session: AsyncSession, *, inst_ids: list[str], bar: str,
                         rule: EvidenceRule | None, now: float
                         ) -> dict[str, EvidenceSnapshot] | None:
    """每个成员一份快照。读库失败 ⇒ 全员空快照 + `error=True`，**不阻断启动**（评审 P1-4）。"""
    if rule is None:
        return None
    try:
        return await _load_snapshots(session, inst_ids=inst_ids, bar=bar, rule=rule, now=now)
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("ai-trade evidence snapshot unavailable (instIds=%s bar=%s): %s",
                       inst_ids, bar, type(exc).__name__)
        try:
            await session.rollback()
        except SQLAlchemyError:
            pass
        return {inst: EvidenceSnapshot(as_of=now, error=True) for inst in inst_ids}


async def _load_snapshots(session: AsyncSession, *, inst_ids: list[str], bar: str,
                          rule: EvidenceRule, now: float) -> dict[str, EvidenceSnapshot]:
    version = rule.ref.version()
    since = now - rule.max_age_days * 86400.0
    horizon = now - rule.ref.max_hold_bars * bar_seconds(bar) * 2
    pooled = {side: await erepo.load_samples(
        session, bar=bar, policy_version=version, side=side, since=since,
        limit=rule.window * POOLED_WINDOW_MULT) for side in SIDES}
    out: dict[str, EvidenceSnapshot] = {}
    for inst in inst_ids:
        samples = {side: await erepo.load_samples(
            session, bar=bar, policy_version=version, side=side, since=since,
            limit=rule.window, inst_id=inst) for side in SIDES}
        await erepo.void_open_before(session, inst_id=inst, bar=bar, before_wall=horizon)
        rows = await erepo.load_open(session, inst_id=inst, bar=bar, policy_version=version)
        out[inst] = EvidenceSnapshot(samples=samples, pooled=pooled,
                                     open_shadows=[shadow_from_row(r) for r in rows], as_of=now)
    await session.commit()
    return out


# ---------------- 向导预览 ----------------
async def evidence_view(session: AsyncSession, *, rule: EvidenceRule, inst_ids: list[str],
                        bar: str, now: float) -> dict:
    """`GET /ai-trade/evidence` 的回执（§5.3）；空库 ⇒ `buckets=[]`。"""
    version = rule.ref.version()
    since = now - rule.max_age_days * 86400.0
    pooled = {side: bucket_stats([v for _, v in await erepo.load_samples(
        session, bar=bar, policy_version=version, side=side, since=since,
        limit=rule.window * POOLED_WINDOW_MULT)], rule) for side in SIDES}
    buckets = []
    for inst in inst_ids:
        for side in SIDES:
            bucket = await _bucket_out(session, rule=rule, inst=inst, bar=bar, side=side,
                                       since=since, pooled=pooled[side])
            if bucket is not None:
                buckets.append(bucket)
    counts = await erepo.recent_live_counts(session, inst_ids=inst_ids, bar=bar,
                                            since=now - RATE_WINDOW_DAYS * 86400.0)
    rates = {inst: {side: round(n / RATE_WINDOW_DAYS, 2) for side, n in by_side.items() if n}
             for inst, by_side in counts.items()}
    return {"bar": bar, "refPolicy": version, "asOf": int(now),
            "rule": {"policy": rule.policy, "minSamples": rule.min_samples,
                     "window": rule.window, "z": rule.z, "maxAgeDays": rule.max_age_days},
            "buckets": buckets,
            "recentShadowRatePerDay": {inst: r for inst, r in rates.items() if r}}


async def _bucket_out(session: AsyncSession, *, rule: EvidenceRule, inst: str, bar: str,
                      side: str, since: float, pooled) -> dict | None:
    rows = await erepo.load_bucket_rows(session, inst_id=inst, bar=bar,
                                        policy_version=rule.ref.version(), side=side,
                                        since=since, limit=rule.window)
    open_n = await erepo.count_open(session, inst_id=inst, bar=bar,
                                    policy_version=rule.ref.version(), side=side)
    if not rows and not open_n:
        return None
    rows = sorted(rows, key=lambda r: r.matured_wall)
    stats = resolve_state(bucket_stats([float(r.net_bps) for r in rows], rule), pooled, rule)
    return {"instId": inst, "side": side, "state": stats.state, **stats_wire(stats),
            "gating": gating_of(stats.state, rule.policy),
            "backfillN": sum(1 for r in rows if r.source == erepo.SOURCE_BACKFILL),
            "openN": open_n,
            "lastMaturedTs": int(rows[-1].matured_wall) if rows else None}


# ---------------- 历史回填 ----------------
@dataclass(frozen=True)
class _Leg:
    run_id: int
    trade_id: int
    inst_id: str
    side: str
    ts: int
    price: float

    @property
    def ref(self) -> str:
        return f"{self.run_id}:{self.trade_id}"


async def _open_legs(session: AsyncSession, *, inst_ids: list[str] | None, bar: str,
                     since: datetime) -> list[_Leg]:
    """逐 run 按 `trades.id` 因果序配对出开仓腿（与 `lock_replay.load_trips` 同一判据）。"""
    stmt = select(StrategyRun.id, StrategyRun.inst_id).where(
        StrategyRun.mode == "ai-trade", StrategyRun.bar == bar, StrategyRun.created_at >= since)
    if inst_ids:
        stmt = stmt.where(StrategyRun.inst_id.in_(inst_ids))
    legs: list[_Leg] = []
    for run_id, inst in (await session.execute(stmt.order_by(StrategyRun.id))).all():
        trades = (await session.execute(select(
            Trade.id, Trade.ts, Trade.price, Trade.pnl, Trade.position_after)
            .where(Trade.run_id == run_id).order_by(Trade.id))).all()
        opened = False
        for trade_id, ts, price, pnl, after in trades:
            flat = after in (0, None)
            if not opened and not flat and not pnl:
                legs.append(_Leg(int(run_id), int(trade_id), inst,
                                 "long" if after > 0 else "short", int(ts), float(price)))
                opened = True
            elif opened and flat:
                opened = False
    return legs


def _segments(legs: list[_Leg], bar_s: int, hold_bars: int) -> list[tuple[int, int]]:
    """把各笔开仓腿需要的区间合并成不相交的段（评审 P1-3 ②：不按标的拉整段 30 天）。"""
    spans = sorted((leg.ts - _PRE_BARS * bar_s, leg.ts + (hold_bars + 2) * bar_s)
                   for leg in legs)
    merged: list[list[int]] = []
    for lo, hi in spans:
        if merged and lo <= merged[-1][1] + bar_s:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return [(lo, hi) for lo, hi in merged]


async def _rest_fetch(inst: str, bar: str, start: int, end: int) -> list[dict]:
    from ..okx.rest_client import get_rest_client

    bar_s = bar_seconds(bar)
    limit = (end - start) // bar_s + 2
    rows = await get_rest_client().get_candles(inst, bar, limit=limit, before=end + bar_s,
                                               after=start - 1)
    return [{"ts": int(c.ts), "o": c.o, "h": c.h, "l": c.l, "c": c.c} for c in rows]


def _leg_shadow(leg: _Leg, frame: list[dict], *, rule: EvidenceRule, bar: str,
                fallback_cost_bps: float) -> tuple[Shadow | None, str | None]:
    """一条开仓腿 → 结束的影子；返回 `(shadow, skip_reason)`。"""
    import pandas as pd

    bar_s = bar_seconds(bar)
    opened_wall = leg.ts + bar_s   # 双时钟保守近似：开仓腿 ts 所在执行周期根的收盘（§2.4-4）
    before = [r for r in frame if r["ts"] + bar_s <= opened_wall]
    if len(before) < _ATR_PERIOD:
        return None, "candlesMissing"
    atr_px = float(atr_series(pd.DataFrame(before), _ATR_PERIOD).iloc[-1])
    shadow = open_shadow(
        inst_id=leg.inst_id, bar=bar, side=leg.side, now_wall=opened_wall, px=leg.price,
        atr_px=atr_px, cost_bps=None, policy=rule.ref, run_id=leg.run_id, round_no=0,
        fallback_cost_bps=fallback_cost_bps, shadow_id=f"bf{leg.run_id}x{leg.trade_id}"[:32])
    if shadow.status != STATUS_OPEN:
        return None, "atrUnavailable"
    for row in (r for r in frame if r["ts"] > shadow.opened_bar_ts):
        shadow = advance_shadow(shadow, row, rule.ref)
        if shadow.status != STATUS_OPEN:
            break
    if shadow.status != "matured":
        return None, "candlesMissing"
    return shadow, None


async def backfill(session: AsyncSession, *, rule: EvidenceRule, inst_ids: list[str] | None,
                   bar: str, days: int, fallback_cost_bps: float, now: float | None = None,
                   deadline_s: float = BACKFILL_DEADLINE_S,
                   fetch: FetchCandles | None = None,
                   clock: Callable[[], float] = time.monotonic) -> dict:
    """从 `trades` 里已有的真实开仓腿回放出证据（§3.6.5）。重复调用幂等，到点截止可续做。

    Raises:
        EvidenceFetchError: 需要拉 K 线的每一段都拉取失败（公共读整体不可用）。
    """
    now = time.time() if now is None else now
    started, fetch = clock(), fetch or _rest_fetch
    since = datetime.fromtimestamp(now - days * 86400, tz=timezone.utc).replace(tzinfo=None)
    legs = await _open_legs(session, inst_ids=inst_ids, bar=bar, since=since)
    skipped = {"candlesMissing": 0, "atrUnavailable": 0, "duplicate": 0, "liveCovered": 0}
    covered = await erepo.runs_with_live_shadows(session, (leg.run_id for leg in legs))
    existing = await erepo.backfill_refs(session, (leg.ref for leg in legs))
    todo = []
    for leg in legs:
        key = ("liveCovered" if leg.run_id in covered
               else "duplicate" if leg.ref in existing else None)
        if key:
            skipped[key] += 1
        else:
            todo.append(leg)
    inserted, done, attempts, failures = 0, 0, 0, 0
    bar_s, truncated = bar_seconds(bar), False
    for inst in sorted({leg.inst_id for leg in todo}):
        inst_legs = [leg for leg in todo if leg.inst_id == inst]
        for lo, hi in _segments(inst_legs, bar_s, rule.ref.max_hold_bars):
            if clock() - started >= deadline_s:
                truncated = True
                break
            seg_legs = [leg for leg in inst_legs if lo <= leg.ts - _PRE_BARS * bar_s <= hi]
            attempts += 1
            try:
                frame = sorted(await fetch(inst, bar, lo, hi), key=lambda r: r["ts"])
            except Exception as exc:  # noqa: BLE001 —— 部分失败按段计入 candlesMissing
                failures += 1
                logger.warning("evidence backfill fetch failed %s %s: %s", inst, bar,
                               type(exc).__name__)
                frame = []
            for leg in seg_legs:
                done += 1
                shadow, why = _leg_shadow(leg, frame, rule=rule, bar=bar,
                                          fallback_cost_bps=fallback_cost_bps)
                if shadow is None:
                    skipped[why] += 1
                    continue
                values = row_values(shadow, source=erepo.SOURCE_BACKFILL, source_ref=leg.ref)
                if await erepo.insert_backfill(session, values):
                    inserted += 1
                else:
                    skipped["duplicate"] += 1
        if truncated:
            break
    if attempts and failures == attempts:
        await session.rollback()
        raise EvidenceFetchError("历史 K 线拉取全部失败（OKX 公共读不可用），请稍后再试")
    await session.commit()
    return {"inserted": inserted, "skipped": skipped, "truncated": truncated,
            "remaining": len(todo) - done,
            "notes": ["影子起点取开仓腿所在执行周期 K 线的收盘时刻（成交时间与 K 线时间是两套时钟，"
                      "取收盘是保守近似）",
                      "成交记录里的数量是张数、换算成名义金额缺合约面值，故回填样本的往返成本一律按"
                      "吃单费率双边 + 半点差估计",
                      "回填只包含真实成交过的开仓，带选择偏差；预览里单列回填笔数"]}


def with_policy(rule: EvidenceRule, policy: str | None) -> EvidenceRule:
    """预览接口的 `policy` 查询参数只影响 gating 派生，不影响统计。"""
    return rule if not policy else replace(rule, policy=policy)
