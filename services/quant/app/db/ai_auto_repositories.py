"""AI 自动交易「历史会话」聚合数据层（ai-trade-stats-dashboard §3.4）。

以 ``strategy_runs.group_id`` 为「一次 AI 自动交易」一等实体，按 group 聚合已持久化的成交为历史会话
列表 / 详情；并提供启动期孤儿对账。**逐成员配对、绝不跨 instId**（R-2）：往返配对逐字移植前端
``tradeTrails`` 因果配对（open→close 单持仓），汇总口径与前端 ``autoTradePnl.summarizeRoundTrips``
严格一致（realized=Σexit.pnl / winRate / best / worst），使历史列表卡片（后端汇总）与详情往返
（前端复算）逐值吻合。收益率分母 = 分组总投入预算（与 ``_finalize_run.totalReturn=realized/budget`` 同源）。

本模块**自包含**（决策 D-0 / R-1）：不 import 同仓在制、未提交的 ``live_auto_repositories.py``，而是镜像其
结构与口径。相对 live-auto 版的 AI-trade 特化：策略名取 ``config.strategyNames``（列表）join；详情 members
多带 ``allocationPct / strategyNames / status / metrics``（详情页画权益图 + AI 决策概况）；孤儿 end_ts 去掉
「最后权益」项（AI-trade 无持久化 equity 表，权益仅 finalize 时一次性 digest 进 metrics_json，P2-3）。

本模块的公开函数经 ``repositories.py`` re-export，调用点仍用 ``repo.X``（沿用该文件既有拆分范式）。
"""
from __future__ import annotations

import json
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import StrategyRun, Trade

_AI_TRADE_MODE = "ai-trade"
_TERMINAL_STATUSES = {"stopped", "done", "error"}


def _load_config(raw: str | None) -> dict:
    """config_json / metrics_json → dict（缺 / 空 / 坏 JSON 安全回 {}，与既有 run_out 容错一致）。"""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _num(value: object) -> float:
    """转有限浮点（非数值 / 非有限 → 0.0），供 leverage / budget / allocationPct 恢复。"""
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return out if out == out and out not in (float("inf"), float("-inf")) else 0.0


def _pair_round_trips(trades: list[Trade]) -> list[tuple[Trade, Trade]]:
    """逐字移植前端 buildPairedTrails：按 (ts, id) 排序后单持仓 open→close 配对。

    side ∈ {long, short} 记为开仓；其余（close）在有未配对开仓时成对，孤儿平仓 / 悬挂开仓丢弃。
    绝不跨 instId——调用方按 run_id（=单一 instId 成员）分桶后逐桶调用（R-2）。
    """
    pairs: list[tuple[Trade, Trade]] = []
    open_trade: Trade | None = None
    for t in sorted(trades, key=lambda x: (x.ts, x.id or 0)):
        if t.side in ("long", "short"):
            open_trade = t
        elif open_trade is not None:
            pairs.append((open_trade, t))
            open_trade = None
    return pairs


def _summarize_pairs(pairs: list[tuple[Trade, Trade]], total_budget_usd: float) -> dict:
    """汇总往返（口径同前端 summarizeRoundTrips）：realized=Σexit.pnl、winRate、best/worst（无样本 0）。"""
    realized = 0.0
    wins = 0
    best = 0.0
    worst = 0.0
    for _entry, exit_trade in pairs:
        pnl = exit_trade.pnl or 0.0
        realized += pnl
        if pnl > 0:
            wins += 1
        if pnl > best:
            best = pnl
        if pnl < worst:
            worst = pnl
    count = len(pairs)
    return {
        "realizedPnlUsd": realized,
        "returnPct": realized / total_budget_usd if total_budget_usd > 0 else 0.0,
        "tradeCount": count,
        "winRate": wins / count if count > 0 else 0.0,
        "bestUsd": best,
        "worstUsd": worst,
    }


def _resolve_total_budget(runs: list[StrategyRun]) -> float:
    """收益率分母：优先 group_total_budget_usd（本方案已填、冗余同值），否则 Σ 成员 config.budgetUsd。"""
    for run in runs:
        if run.group_total_budget_usd is not None:
            return float(run.group_total_budget_usd)
    return sum(_num(_load_config(run.config_json).get("budgetUsd")) for run in runs)


def _distinct_inst_ids(runs: list[StrategyRun]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for run in runs:
        if run.inst_id not in seen:
            seen.add(run.inst_id)
            out.append(run.inst_id)
    return out


def _config_strategy_names(cfg: dict) -> list[str]:
    """AI-trade 特化：策略名列表取 config.strategyNames（多策略），过滤空串。"""
    names = cfg.get("strategyNames")
    if isinstance(names, list):
        return [str(n) for n in names if n]
    return []


def _group_strategy_name(runs: list[StrategyRun]) -> str:
    """整组策略名：取首个非空成员的 strategyNames 列表 join（AI-trade 是多策略，非 live-auto 的单值）。"""
    for run in runs:
        names = _config_strategy_names(_load_config(run.config_json))
        if names:
            return " / ".join(names)
    return ""


def _group_status(runs: list[StrategyRun]) -> str:
    """整组状态：全部成员达终态 → stopped，否则 running（历史列表已排除活跃组，此处保守判定）。"""
    return "stopped" if all(r.status in _TERMINAL_STATUSES for r in runs) else "running"


def _group_started_ts(runs: list[StrategyRun], trades_by_run: dict[int, list[Trade]]) -> int:
    """会话起点：group_started_ts（各成员冗余同值）→ Σ 成员 start_ts 最小 → 首笔成交 ts → 0。"""
    for run in runs:
        if run.group_started_ts:
            return int(run.group_started_ts)
    starts = [int(r.start_ts) for r in runs if r.start_ts]
    if starts:
        return min(starts)
    first = [trades_by_run[r.id][0].ts for r in runs if trades_by_run.get(r.id)]
    return int(min(first)) if first else 0


def _aggregate_group(runs: list[StrategyRun], trades_by_run: dict[int, list[Trade]]) -> dict:
    """把一个 group 的成员 runs + 成交聚合为共享字段（列表 / 详情共用；逐成员配对不跨标的）。"""
    total_budget = _resolve_total_budget(runs)
    all_pairs: list[tuple[Trade, Trade]] = []
    end_candidates: list[int] = []
    for run in runs:
        member_trades = trades_by_run.get(run.id, [])
        all_pairs.extend(_pair_round_trips(member_trades))
        if run.end_ts:
            end_candidates.append(int(run.end_ts))
        if member_trades:
            end_candidates.append(int(member_trades[-1].ts))
    started_ts = _group_started_ts(runs, trades_by_run)
    return {
        "strategyName": _group_strategy_name(runs),
        "bar": runs[0].bar if runs else "",
        "instIds": _distinct_inst_ids(runs),
        "startedTs": started_ts,
        "endedTs": max(end_candidates) if end_candidates else started_ts,
        "status": _group_status(runs),
        "totalBudgetUsd": total_budget,
        "summary": _summarize_pairs(all_pairs, total_budget),
    }


async def _load_trades_by_run(session: AsyncSession, run_ids: list[int]) -> dict[int, list[Trade]]:
    """一次取回给定 run 的全部成交（按 ts,id 升序），分桶为 {run_id: [Trade,...]}（配对输入）。"""
    if not run_ids:
        return {}
    rows = (await session.execute(
        select(Trade).where(Trade.run_id.in_(run_ids))
        .order_by(Trade.ts.asc(), Trade.id.asc()))).scalars().all()
    by_run: dict[int, list[Trade]] = defaultdict(list)
    for trade in rows:
        by_run[trade.run_id].append(trade)
    return by_run


async def list_ai_trade_groups(session: AsyncSession, *, limit: int = 20, offset: int = 0,
                               exclude_group_id: str | None = None) -> dict:
    """历史会话列表：group_id 非空、按 group_started_ts DESC、可排除当前运行中 group（§3.4 / §5.1）。

    3 查询/页（去重分组分页 + 成员 runs + 成交），逐成员配对聚合每组 KPI；返回
    ``{items, total, limit, offset}``。空历史 → ``items=[]``、``total=0``。
    """
    where = [StrategyRun.mode == _AI_TRADE_MODE, StrategyRun.group_id.is_not(None)]
    if exclude_group_id is not None:
        where.append(StrategyRun.group_id != exclude_group_id)

    total = (await session.execute(
        select(func.count(func.distinct(StrategyRun.group_id))).where(*where))).scalar() or 0
    page_rows = (await session.execute(
        select(StrategyRun.group_id).where(*where).group_by(StrategyRun.group_id)
        .order_by(func.max(StrategyRun.group_started_ts).desc())
        .limit(limit).offset(offset))).all()
    group_ids = [row[0] for row in page_rows]
    if not group_ids:
        return {"items": [], "total": int(total), "limit": limit, "offset": offset}

    runs = (await session.execute(
        select(StrategyRun).where(StrategyRun.group_id.in_(group_ids))
        .order_by(StrategyRun.id.asc()))).scalars().all()
    trades_by_run = await _load_trades_by_run(session, [r.id for r in runs])
    runs_by_group: dict[str, list[StrategyRun]] = defaultdict(list)
    for run in runs:
        runs_by_group[run.group_id].append(run)

    items = []
    for gid in group_ids:   # 保持分页顺序（group_started_ts DESC）
        agg = _aggregate_group(runs_by_group[gid], trades_by_run)
        summary = agg.pop("summary")
        items.append({"groupId": gid, **agg, **summary})
    return {"items": items, "total": int(total), "limit": limit, "offset": offset}


async def get_ai_trade_group_detail(session: AsyncSession, group_id: str) -> dict | None:
    """单会话详情（§3.4 / §5.1）：会话头 + 逐成员（含 metrics/allocationPct/strategyNames）+ 后端 summary。

    成交行以 ORM ``Trade`` 返回，由 API 层 ``trade_out`` 序列化；``metrics`` 为解析后的 metrics_json（含可选
    equityDigest / decisionsDigest / aiStats / finalSummary / reconciled），供详情页画权益图与 AI 概况、
    并在缺失时优雅降级。组不存在 → None。
    """
    runs = (await session.execute(
        select(StrategyRun).where(StrategyRun.group_id == group_id)
        .order_by(StrategyRun.id.asc()))).scalars().all()
    if not runs:
        return None
    trades_by_run = await _load_trades_by_run(session, [r.id for r in runs])
    members = []
    for run in runs:
        cfg = _load_config(run.config_json)
        members.append({
            "runId": run.id, "instId": run.inst_id,
            "leverage": _num(cfg.get("leverage")), "budgetUsd": _num(cfg.get("budgetUsd")),
            "allocationPct": _num(cfg.get("allocationPct")),
            "strategyNames": _config_strategy_names(cfg), "status": run.status,
            "metrics": _load_config(run.metrics_json),
            "trades": trades_by_run.get(run.id, []),
        })
    agg = _aggregate_group(runs, trades_by_run)
    return {
        "groupId": group_id, "strategyName": agg["strategyName"], "bar": agg["bar"],
        "startedTs": agg["startedTs"], "endedTs": agg["endedTs"], "status": agg["status"],
        "totalBudgetUsd": agg["totalBudgetUsd"], "members": members, "summary": agg["summary"],
    }


async def reconcile_orphan_ai_trade_runs(session: AsyncSession) -> int:
    """启动期孤儿对账（§3.5 / R-3）：running 的 ai-trade run 回填 end_ts + 置 stopped + reconciled=true。

    幂等：二次运行无 running 行即 0 改。end_ts 取「最后成交 ts / group_started_ts / start_ts」之最大
    （AI-trade 无持久化 equity 表，去掉 live-auto 版的「最后权益」项，P2-3），避免历史列表「幽灵进行中」。
    返回被对账条数。
    """
    orphans = (await session.execute(
        select(StrategyRun).where(
            StrategyRun.mode == _AI_TRADE_MODE, StrategyRun.status == "running"))).scalars().all()
    if not orphans:
        return 0
    for run in orphans:
        run.status = "stopped"
        run.end_ts = await _resolve_orphan_end_ts(session, run)
        metrics = _load_config(run.metrics_json)
        metrics["reconciled"] = True
        run.metrics_json = json.dumps(metrics, ensure_ascii=False)
    await session.commit()
    return len(orphans)


async def _resolve_orphan_end_ts(session: AsyncSession, run: StrategyRun) -> int:
    """孤儿 run 的 end_ts：max(最后成交 ts, group_started_ts, start_ts)；全空回 0（无 equity 表项）。"""
    last_trade = (await session.execute(
        select(func.max(Trade.ts)).where(Trade.run_id == run.id))).scalar()
    candidates = [int(v) for v in (last_trade, run.group_started_ts, run.start_ts) if v]
    return max(candidates) if candidates else 0
