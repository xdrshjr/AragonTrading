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
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
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


@dataclass(frozen=True)
class _Trip:
    """一笔完整往返（含腿费与所属 run 的口径标记，cost-truth §3.2 B-5）。"""

    entry: Trade
    exit: Trade
    leg_fee_usd: float      # Σ 上一笔平仓之后至本笔平仓（含）的**全部**腿 fee
    cost_basis: str         # 所属 run 的 costBasis（"net" | "gross"）

    @property
    def net_pnl(self) -> float:
        """净额（已扣双边手续费）：`"gross"` 的 run 按 `pnl − legFee` 还原。"""
        pnl = self.exit.pnl or 0.0
        return pnl if self.cost_basis == "net" else pnl - self.leg_fee_usd

    @property
    def gross_pnl(self) -> float:
        """毛额还原（净额行加回腿费）——捕获率分子必须与毛额分母同口径（P1-3）。"""
        pnl = self.exit.pnl or 0.0
        return pnl + self.leg_fee_usd if self.cost_basis == "net" else pnl


def _pair_round_trips(trades: list[Trade], cost_basis: str = "gross") -> list[_Trip]:
    """逐字移植前端 buildPairedTrails：按**因果序**（id 升序 = 落库序）单持仓 open→close 配对。

    side ∈ {long, short} 记为开仓；其余（close）在有未配对开仓时成对，孤儿平仓 / 悬挂开仓丢弃。
    绝不跨 instId——调用方按 run_id（=单一 instId 成员）分桶后逐桶调用（R-2）。

    排序键不能用 ``ts``：AI 自动交易的 ``trades.ts`` 由两套时钟写入（K 线轮 = bar 开盘时刻 /
    定时唤醒轮与 monitor 轮 = 墙钟），按 ts 排序会破坏真实的开→平交替 —— 对内逆序整笔丢弃、
    跨对重叠则错配加丢弃（诊断 ai-trade-stats-missing-round-trips）。``trades.id`` 是落库自增，
    即真实发生顺序，实测生产库按 id 配对 161 笔平仓零孤儿。仅本函数改排序键：
    ``_load_trades_by_run`` 的 SQL 仍按 (ts, id) 升序，故 ``_aggregate_group`` 的 endedTs 与
    ``_group_started_ts`` 的「首元素 == 最早 ts」语义原样成立。

    **腿费按区间累计**（B-5）：一笔往返的费用 = 自上一笔平仓（不含）起、到本笔平仓（含）止的
    **全部**成交腿 fee 之和。按「配对的那条开仓腿的 fee」计是错的——部分成交会产生多条开仓腿而
    本函数只保留最后一条，会漏记先前分批的费用。腿费累计与配对必须钉死**同一个**排序键（P2-4），
    否则费用会串窗。
    """
    trips: list[_Trip] = []
    open_trade: Trade | None = None
    leg_fee = 0.0
    for t in sorted(trades, key=lambda x: x.id or 0):
        leg_fee += float(getattr(t, "fee", 0.0) or 0.0)
        if t.side in ("long", "short"):
            open_trade = t
        elif open_trade is not None:
            trips.append(_Trip(open_trade, t, leg_fee, cost_basis))
            open_trade = None
            leg_fee = 0.0
    return trips


def _summarize_pairs(trips: list[_Trip], total_budget_usd: float) -> dict:
    """汇总往返（口径同前端 summarizeRoundTrips）：realized/winRate/best/worst 走**净额**。

    另出**利润捕获率**五键（ai-trade-profit-protection-adaptive §2.4.3，口径同前端
    ``summarizeProfitCapture`` 且公式逐字对应）：``captureRatio = Σrealized / Σpeak``，只统计
    ``peak > 0`` 的往返；``peak`` 缺失（保护层未装配 / 老库缺列）或 ≤0（全程未浮盈）的往返计入
    ``noPeakCount`` 单列，**不进分母**——把它们按 0 计入会让捕获率变成 0 或 ∞。

    **捕获率五键必须整体留在毛额口径**（cost-truth P1-3）：分母 ``peakPnlUsd`` 来自
    ``trades.peak_pnl``，而该列由利润保护层按**本地派生毛浮盈**落库（``profit_guard.py`` 自算一份，
    不经 ``unrealized_usd``），本轮有意不动它。若分子换成净额而分母仍是毛额，比值会系统性下移一个
    往返成本，且**老会话的历史 ``captureRatio`` 会被追溯改小**——那是用一个正确的修复打坏另一个
    已发布的 KPI。故 ``capturedPnlUsd`` 恒取 ``trip.gross_pnl``，与分母同维（G-14 双向断言）。
    """
    realized = 0.0
    gross_total = 0.0
    fee_total = 0.0
    wins = 0
    best = 0.0
    worst = 0.0
    peak_total = 0.0
    captured_total = 0.0
    capture_pairs = 0
    no_peak = 0
    for trip in trips:
        pnl = trip.net_pnl
        realized += pnl
        gross_total += trip.gross_pnl
        fee_total += trip.leg_fee_usd
        if pnl > 0:
            wins += 1
        if pnl > best:
            best = pnl
        if pnl < worst:
            worst = pnl
        peak = getattr(trip.exit, "peak_pnl", None)   # 老库缺列读到 None 而非 AttributeError
        if peak is not None and peak > 0:
            peak_total += float(peak)
            captured_total += trip.gross_pnl          # 分子分母同为毛额（P1-3）
            capture_pairs += 1
        else:
            no_peak += 1
    count = len(trips)
    return {
        "realizedPnlUsd": realized,
        "returnPct": realized / total_budget_usd if total_budget_usd > 0 else 0.0,
        "tradeCount": count,
        "winRate": wins / count if count > 0 else 0.0,
        "bestUsd": best,
        "worstUsd": worst,
        "feeUsd": fee_total,
        "grossPnlUsd": gross_total,
        "costBasisMixed": len({t.cost_basis for t in trips}) > 1,
        "peakPnlUsd": peak_total,
        "capturedPnlUsd": captured_total,
        "captureRatio": captured_total / peak_total if peak_total > 0 else 0.0,
        "capturePairs": capture_pairs,
        "noPeakCount": no_peak,
    }


def _run_cost_basis(cfg: dict) -> tuple[str, bool]:
    """该 run 的盈亏口径与「是否为推定」（cost-truth P0-3）。

    ``config_json`` 里有 ``costBasis`` 即为**已知**；缺键的老 run 走
    ``ai_trade_history_legacy_cost_basis`` **推定**（默认 ``"gross"``，对出厂配置正确）。已按阶段一
    §7.5 runbook 提前打开 ``ai_trade_net_pnl_enabled`` 的部署必须把它置 ``"net"``，否则那批**已是
    净额**的历史行会被聚合层二次扣费——与本轮要修的缺陷同量级、方向相反。UI 必须把「推定」与
    「已知」分开显示，故第二个返回值恒随回执下发。
    """
    basis = cfg.get("costBasis")
    if basis in ("net", "gross"):
        return basis, False
    legacy = str(get_settings().ai_trade_history_legacy_cost_basis or "gross")
    return ("net" if legacy == "net" else "gross"), True


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
    all_pairs: list[_Trip] = []
    end_candidates: list[int] = []
    assumed = False
    for run in runs:
        member_trades = trades_by_run.get(run.id, [])
        basis, basis_assumed = _run_cost_basis(_load_config(run.config_json))
        assumed = assumed or basis_assumed
        all_pairs.extend(_pair_round_trips(member_trades, basis))
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
        "costBasisAssumed": assumed,
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
            "costBasis": _run_cost_basis(cfg)[0],   # 该成员 run 的盈亏口径（推定与否见组级标记）
            "metrics": _load_config(run.metrics_json),
            "trades": trades_by_run.get(run.id, []),
        })
    agg = _aggregate_group(runs, trades_by_run)
    summary = {**agg["summary"], **_merge_acceptance(members, runs)}
    return {
        "groupId": group_id, "strategyName": agg["strategyName"], "bar": agg["bar"],
        "startedTs": agg["startedTs"], "endedTs": agg["endedTs"], "status": agg["status"],
        "totalBudgetUsd": agg["totalBudgetUsd"], "costBasisAssumed": agg["costBasisAssumed"],
        "members": members, "summary": summary,
    }


def _merge_acceptance(members: list[dict], runs: list[StrategyRun]) -> dict:
    """把各成员 run 的 `acceptanceInput` 合并成**一张**组级验收卡（[P1-9]）。

    历史是**按成员 run 落库**的，而 `build_acceptance` 的产物是一张**已判定的卡**——前端无法把
    3 张卡合并成 1 张（比例类指标不可平均），`merge_inputs` 只存在于后端，故合并必须在这里做。

    **任一成员缺 `acceptanceInput`（本特性上线前的老记录）⇒ 整键缺席**：绝不用部分成员的数据
    冒充整组结论。周期契约取自成员 run 的 `config.tradingHorizon`（分组共享同一份）。
    """
    from ..ai_auto.horizon_acceptance import input_from_wire, merge_bundle
    wires = [(m.get("metrics") or {}).get("aiStats", {}).get("acceptanceInput")
             for m in members]
    if not wires or any(w is None for w in wires):
        return {}
    inputs = [inp for inp in (input_from_wire(w) for w in wires) if inp is not None]
    rule = _group_horizon_rule(runs)
    if rule is None or not inputs:
        return {}
    fundings = [(m.get("metrics") or {}).get("aiStats", {}).get("funding")
                for m in members]
    merged = merge_bundle(inputs, rule=rule, settings=get_settings(), fundings=fundings)
    return {"horizonAcceptance": merged["acceptance"], "exits": merged["exits"],
            "funding": merged["funding"]}


def _group_horizon_rule(runs: list[StrategyRun]):
    """组级周期契约（各成员共享一份，取第一条可解析的）；未装配 ⇒ `None`。"""
    from ..ai_auto.hold_horizon import rule_from_wire
    for run in runs:
        wire = _load_config(run.config_json).get("tradingHorizon")
        rule = rule_from_wire(wire)
        if rule is not None:
            return rule
    return None


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
