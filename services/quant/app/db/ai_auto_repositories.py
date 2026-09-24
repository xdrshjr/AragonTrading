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
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..ai_auto.accounting_replay import TradeAccountingAccumulator
from ..ai_auto.trade_economics import (TripPnlInputs, merge_economics_snapshots,
                                      resolve_trip_economics)
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
    economics: dict | None = None

    @property
    def net_pnl(self) -> float:
        """净额（已扣双边手续费）：`"gross"` 的 run 按 `pnl − legFee` 还原。"""
        resolved = resolve_trip_economics(TripPnlInputs(
            self.cost_basis, self.exit.pnl, self.leg_fee_usd, 0.0))
        return _num(resolved.trading_net_usd)

    @property
    def gross_pnl(self) -> float:
        """毛额还原（净额行加回腿费）——捕获率分子必须与毛额分母同口径（P1-3）。"""
        resolved = resolve_trip_economics(TripPnlInputs(
            self.cost_basis, self.exit.pnl, self.leg_fee_usd, 0.0))
        return _num(resolved.gross_pnl_usd)


def _pair_round_trips(trades: list[Trade], cost_basis: str = "gross") -> list[_Trip]:
    """按数据库 id 复用增量归并器；派生均价与数量不回写原始腿。"""
    accumulator = TradeAccountingAccumulator({"costBasis": cost_basis})
    accumulator.restore([_accounting_trade(t) for t in trades])
    return [_legacy_trip(row, cost_basis) for row in accumulator.completed_trips]


def _accounting_trade(trade: Trade) -> dict:
    """完整原始经济输入及 explain 来源，保留空值和事件身份。"""
    row = {name: getattr(trade, name, None) for name in
           ("id", "side", "ts", "price", "qty", "fee", "pnl", "reason")}
    row.update(runId=getattr(trade, "run_id", None),
        positionAfter=getattr(trade, "position_after", None),
        explain=_load_config(getattr(trade, "explain_json", None)),
        stopLossPrice=getattr(trade, "stop_loss_price", None),
        takeProfitPrice=getattr(trade, "take_profit_price", None),
        peakPnl=getattr(trade, "peak_pnl", None))
    return row


def _legacy_trade(row: dict, **overrides):
    return SimpleNamespace(**{**row, "explain_json": json.dumps(row.get("explain")),
        "stop_loss_price": row.get("stopLossPrice"),
        "take_profit_price": row.get("takeProfitPrice"),
        "peak_pnl": row.get("peakPnl"), **overrides})


def _legacy_trip(row: dict, cost_basis: str) -> _Trip:
    entry = _legacy_trade(row["entryTrade"], price=row["entryPrice"], qty=row["qty"])
    close = _legacy_trade(row["closeTrade"], pnl=row["recordedPnl"],
                          price=row["exitPrice"])
    return _Trip(entry, close, row["recordedFeeUsd"], cost_basis, row)


def _run_economics(run: StrategyRun, trades: list[Trade]) -> dict:
    config = _load_config(run.config_json)
    accumulator = TradeAccountingAccumulator({"runId": run.id,
        "costBasis": config.get("costBasis"), "ctVal": config.get("ctVal"),
        "asOfTs": run.end_ts or run.start_ts})
    return accumulator.restore([_accounting_trade(t) for t in trades])


def _metrics_with_economics(run: StrategyRun, trades: list[Trade]) -> dict:
    metrics = _load_config(run.metrics_json)
    economics = _run_economics(run, trades)
    stats = metrics.setdefault("aiStats", {})
    stats.setdefault("integrity", {})["economics"] = economics
    for target, source in (("winRate", "winRate"),
                           ("profitFactor", "profitFactor"),
                           ("avgPnl", "avgPnlUsd")):
        if economics[source] is not None:
            metrics[target] = economics[source]
    metrics["roundTripCount"] = economics["roundTripCount"]
    metrics["tradeCount"] = len(trades)
    return metrics


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
        "roundTripCount": count,
        "winRate": wins / count if count > 0 else 0.0,
        # [P1-7] `wins` 是 E-2 池化所需的**分子**，纯加法键（既有键的值逐位不变，G-19 双向断言）。
        # 靠 `round(winRate × tradeCount)` 反推分子是把浮点误差引进判据，明令禁止。
        "wins": wins,
        "bestUsd": best,
        "worstUsd": worst,
        "feeUsd": fee_total,
        "grossPnlUsd": gross_total,
        "costBasisMixed": len({t.cost_basis for t in trips}) > 1,
        # E-6（ai-trade-loss-and-liveness-hardening）：`costBasisMixed` 是个布尔，说不出
        # 「多少笔」——用户因此无法判断这份混装到底污染了多大比例的读数（实测 66/94 条有成交
        # 的 run 全腿 fee==0）。**同一处现算**，绝不在第二处再判一次口径。
        "costBasisCounts": {
            "net": sum(1 for t in trips if t.cost_basis == "net"),
            "gross": sum(1 for t in trips if t.cost_basis != "net"),
        },
        "peakPnlUsd": peak_total,
        "capturedPnlUsd": captured_total,
        "captureRatio": captured_total / peak_total if peak_total > 0 else 0.0,
        "capturePairs": capture_pairs,
        "noPeakCount": no_peak,
        **_green_to_red_keys(trips),
    }


# 锁平后「立即同向重开」的判定窗口（秒）。前端 `aiTradeCapture.ts` 有同名常量，由双向断言锁住。
LOCK_REENTRY_WINDOW_S = 1800
_LOCK_REASON_PREFIX = "profit_lock_"


def _green_to_red_keys(trips: list[_Trip]) -> dict:
    """盈转亏与收益锁的四个纯加法键（profit-lock 轴 C-3；口径同前端 `summarizeProfitCapture`）。

    - ``greenToRedCount``：曾浮盈（``peak_pnl > 0``）却以净亏（``net <= 0``）离场的往返数；
    - ``greenToRedGivebackUsd``：这些往返的 ``Σ(peak − gross)``，与捕获率同为毛口径（P1-3）；
    - ``lockExitCount``：收益锁离场的往返数；
    - ``lockReentryCount``：锁平后**下一笔**往返同向、且开仓距锁平 ≤ 窗口的次数——「锁平 → 立即
      同向重开」的手续费失血此前完全不可观测（评审 RV-P1-9）。按 trips 时间序相邻两笔判定。
    """
    count, giveback, lock_exits, reentries = 0, 0.0, 0, 0
    for idx, trip in enumerate(trips):
        peak = getattr(trip.exit, "peak_pnl", None)
        if peak is not None and peak > 0 and trip.net_pnl <= 0:
            count += 1
            giveback += float(peak) - trip.gross_pnl
        if not str(getattr(trip.exit, "reason", "") or "").startswith(_LOCK_REASON_PREFIX):
            continue
        lock_exits += 1
        nxt = trips[idx + 1] if idx + 1 < len(trips) else None
        # 同一 run（同一标的）内才算重开：trips 是逐 run 拼接的，跨 run 相邻不代表先后关系。
        if (nxt is not None and nxt.entry.run_id == trip.exit.run_id
                and nxt.entry.side == trip.entry.side
                and 0 <= int(nxt.entry.ts) - int(trip.exit.ts) <= LOCK_REENTRY_WINDOW_S):
            reentries += 1
    return {"greenToRedCount": count, "greenToRedGivebackUsd": giveback,
            "lockExitCount": lock_exits, "lockReentryCount": reentries}


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
        "summary": {**_summarize_pairs(all_pairs, total_budget),
            "economics": merge_economics_snapshots([
                _run_economics(run, trades_by_run.get(run.id, [])) for run in runs])},
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
                               exclude_group_id: str | None = None,
                               exclude_statuses: tuple[str, ...] = ()) -> dict:
    """历史会话列表：group_id 非空、按 group_started_ts DESC、可排除当前运行中 group（§3.4 / §5.1）。

    3 查询/页（去重分组分页 + 成员 runs + 成交），逐成员配对聚合每组 KPI；返回
    ``{items, total, limit, offset}``。空历史 → ``items=[]``、``total=0``。

    ``exclude_statuses``（ai-trade-pause-resume D-8）：按 run **状态**排除（默认空元组 ⇒ 既有
    行为逐字不变）。它与 ``exclude_group_id`` 分工互补：后者是**内存派生**的，冷启后内存里什么
    都没有，一个仍持真实仓位的 ``paused`` 分组会同时出现在「历史会话」里——用户会看到同一次交易
    既「已结束」又「可继续」。
    """
    where = [StrategyRun.mode == _AI_TRADE_MODE, StrategyRun.group_id.is_not(None)]
    if exclude_group_id is not None:
        where.append(StrategyRun.group_id != exclude_group_id)
    if exclude_statuses:
        where.append(StrategyRun.status.notin_(tuple(exclude_statuses)))

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
            "metrics": _metrics_with_economics(run, trades_by_run.get(run.id, [])),
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


# 一组的指标要「下结论」所需的最小历史会话数（tradability-and-open-funnel 轴 E）。低于它照常给出
# 全部数字但 `conclusive=false`，前端显式写「样本不足，不下结论」——用 1~2 局历史宣称「哪档更赚」
# 正是本仓库明令禁止的假阳性观测（沿用统计看板既有的最小样本门禁范式）。
_PRESET_COMPARISON_MIN_SAMPLES = 3


async def list_ai_trade_preset_comparison(session: AsyncSession) -> dict:
    """按纪律预设分组聚合**历史真实资金会话**的表现（轴 E · §5.2；只读 `metrics_json`，零迁移）。

    它偿还的是第 1 轮的 G-17 欠账的一半：「这组阈值到底是让系统赚钱还是让系统停摆」。另一半由轴 A
    的确定性探针秒级回答。**刻意不做两臂 LLM 回测对照**——实测 50 分钟跑不完两臂，且回测与实盘的
    仓位求解仍不同构（`p_win` 在回测侧恒 `None` ⇒ 凯利项恒不参与 `min`），结论不可迁移。

    分组键取 `metricsJson.aiStats.disciplinePreset`（第 1 轮起落库）；缺键的老 run 归 `unknown`
    并如实标注——**不能**把它们默默算进 `balanced`，那会用一批不知道开着什么纪律的历史去污染结论。

    Returns:
        `{"groups": [{preset, samples, conclusive, netPnlUsd, roundTrips, winRate,
        edgeVetoRate, openConversion}], "minSamples": N}`。
    """
    runs = (await session.execute(
        select(StrategyRun).where(StrategyRun.mode == _AI_TRADE_MODE,
                                  StrategyRun.group_id.is_not(None))
        .order_by(StrategyRun.id.asc()))).scalars().all()
    if not runs:
        return {"groups": [], "minSamples": _PRESET_COMPARISON_MIN_SAMPLES}
    trades_by_run = await _load_trades_by_run(session, [r.id for r in runs])
    by_group: dict[str, list[StrategyRun]] = defaultdict(list)
    for run in runs:
        by_group[run.group_id].append(run)
    buckets: dict[str, list[dict]] = defaultdict(list)
    for members in by_group.values():
        buckets[_group_preset(members)].append(_group_comparison_row(members, trades_by_run))
    return {"groups": [_summarize_preset(preset, rows)
                       for preset, rows in sorted(buckets.items())],
            "minSamples": _PRESET_COMPARISON_MIN_SAMPLES}


def _group_preset(runs: list[StrategyRun]) -> str:
    """该组启动时生效的纪律预设；缺键的老 run ⇒ `"unknown"`（**不归进任何具名档**）。"""
    for run in runs:
        preset = (_load_config(run.metrics_json).get("aiStats") or {}).get("disciplinePreset")
        if preset:
            return str(preset)
    return "unknown"


def _group_comparison_row(runs: list[StrategyRun], trades_by_run: dict[int, list]) -> dict:
    """一个历史 group 的对照读数（净额盈亏 / 往返数 / 胜率 / 门禁否决率 / 漏斗转化率）。

    净额与往返配对**逐字复用** `_aggregate_group`——历史列表卡片与本卡必须是同一个数，
    另算一份就是第二个口径。`edgeVetoRate` / `openConversion` 取各成员 `aiStats` 的**加权/合并**
    读数：算不出的一律 `None`（**不填 0 冒充**，0 与「没有这个读数」是两个结论）。
    """
    agg = _aggregate_group(runs, trades_by_run)
    summary = agg["summary"]
    stats = [(_load_config(r.metrics_json).get("aiStats") or {}) for r in runs]
    gated = sum(_num((s.get("edgeGate") or {}).get("gated")) for s in stats)
    veto = sum(_num((s.get("edgeGate") or {}).get("veto")) for s in stats)
    intents = sum(_num((s.get("openFunnel") or {}).get("intents")) for s in stats)
    filled = sum(_num((s.get("openFunnel") or {}).get("filled")) for s in stats)
    # 方向轴（directional-symmetry 轴 D · D-3）：两个纯加法读点，使「历史上这台机器到底有没有
    # 做空过」可以跨会话回答。老 run / 总闸关的会话缺 `directionBalance` ⇒ 按 0 计**但也不进
    # 任何分母**（`_summarize_preset` 只把它们相加、不做比值），绝不把「没有这个字段」画成
    # 「做空 0 次」。
    balance = [(s.get("openFunnel") or {}).get("directionBalance") or {} for s in stats]
    long_intents = sum(_num(b.get("longIntents")) for b in balance)
    short_intents = sum(_num(b.get("shortIntents")) for b in balance)
    # E-2（realized-edge-calibration D-2）：返回**分子分母**而不是比值——`_summarize_preset` 求和
    # 后再除即得池化口径。合入前这三个比例走的是「各组比值的算术平均」，而各组的分母从 1 笔到
    # 39 笔不等（实测池化胜率 40.74% vs 等权 36.11%，差 4.63 pp）。
    return {"netPnlUsd": summary["realizedPnlUsd"], "roundTrips": summary["tradeCount"],
            "wins": int(summary.get("wins") or 0),
            "edgeGated": gated, "edgeVeto": veto,
            "openIntents": intents, "openFilled": filled,
            "longIntents": long_intents, "shortIntents": short_intents}


def _summarize_preset(preset: str, rows: list[dict]) -> dict:
    """一档预设的汇总：盈亏与往返数取**和**，三个比例类指标一律**池化**（Σ分子 / Σ分母）。

    `samples`（局数）与比例的分母是两个东西：前者是这一档有多少局历史，后者是这一档**全部**局
    加起来有多少笔往返 / 多少个受约束轮。合入前取的是各局比值的算术平均，等于让一个 1 笔往返的
    局与一个 39 笔的局对结论有**完全相同**的权重——这是一次**有意的口径变更**，卡片须标注
    「池化口径」，老截图上的数字会变（§十一.2 已如实记账）。
    """
    return {"preset": preset, "samples": len(rows),
            "conclusive": len(rows) >= _PRESET_COMPARISON_MIN_SAMPLES,
            "netPnlUsd": sum(_num(r["netPnlUsd"]) for r in rows),
            "roundTrips": int(sum(_num(r["roundTrips"]) for r in rows)),
            "winRate": _pooled_rate(rows, "wins", "roundTrips"),
            "edgeVetoRate": _pooled_rate(rows, "edgeVeto", "edgeGated"),
            "openConversion": _pooled_rate(rows, "openFilled", "openIntents"),
            # 方向轴（directional-symmetry D-3）：两个**计数**而不是比值——`directionBalance`
            # 缺席的老 run 贡献 0，把它们除一除就会把「这一档没有方向数据」画成「这一档从不做空」。
            "longIntents": int(sum(_num(r.get("longIntents")) for r in rows)),
            "shortIntents": int(sum(_num(r.get("shortIntents")) for r in rows))}


def _pooled_rate(rows: list[dict], numerator: str, denominator: str) -> float | None:
    """池化比例 `Σ分子 / Σ分母`；分母为 0 ⇒ `None`（**绝不填 0 冒充**，0 是另一个结论）。"""
    total = sum(_num(r.get(denominator)) for r in rows)
    if total <= 0:
        return None
    return sum(_num(r.get(numerator)) for r in rows) / total


# ============ 跨会话盈利体检的取数层（realized-edge-calibration 轴 B · §3.2.2）============
@dataclass(frozen=True)
class TripQuery:
    """轴 B 端点的过滤条件（[P1-11] 形参聚合，满足 CLAUDE.md 的「≤5」）。全部缺省 = 全部历史。"""

    inst_id: str | None = None
    bar: str | None = None
    since_ts: int | None = None
    preset: str | None = None
    horizon_profile: str | None = None


async def load_profitability_trips(session: AsyncSession, query: TripQuery) -> list[dict]:
    """兼容旧调用方的净额子集；正式总体统计读取完整 dataset。"""
    from .ai_profitability_repositories import load_profitability_dataset

    dataset = await load_profitability_dataset(session, query)
    return [row for row in dataset.samples if row["netEligible"]]


def _run_matches(run: StrategyRun, query: TripQuery) -> bool:
    """run 级过滤（标的 / 周期 / 起始时间）；`bar` 从 `config.bar` 取（`StrategyRun` 无该列）。"""
    if query.inst_id and run.inst_id != query.inst_id:
        return False
    if query.since_ts and int(run.group_started_ts or run.start_ts or 0) < int(query.since_ts):
        return False
    return not (query.bar and _run_bar(run) != query.bar)


def _row_matches(row: dict, query: TripQuery) -> bool:
    """trip 级过滤（预设 / 周期模式）：缺该维度的往返在**指定了该过滤条件**时不计入。"""
    if query.preset and row.get("preset") != query.preset:
        return False
    return not (query.horizon_profile and row.get("horizonProfile") != query.horizon_profile)


def _run_bar(run: StrategyRun) -> str | None:
    return _load_config(run.config_json).get("bar") or None


def _run_dimensions(run: StrategyRun, cfg: dict, assumed: bool) -> dict:
    """run 级维度（切片用）：标的 / 周期 / 预设 / 周期模式 / 口径是否为推定。"""
    stats = (_load_config(run.metrics_json).get("aiStats") or {})
    horizon = cfg.get("tradingHorizon") or {}
    return {"instId": run.inst_id, "bar": cfg.get("bar") or None,
            "preset": stats.get("disciplinePreset") or "unknown",
            "horizonProfile": (horizon.get("profile") if isinstance(horizon, dict) else None),
            "costBasisAssumed": bool(assumed)}


async def reconcile_orphan_ai_trade_runs(session: AsyncSession, *,
                                         resume_enabled: bool = False) -> int:
    """启动期孤儿对账（§3.5 / R-3）：running 的 ai-trade run 回填 end_ts + 置 stopped + reconciled=true。

    幂等：二次运行无 running 行即 0 改。end_ts 取「最后成交 ts / group_started_ts / start_ts」之最大
    （AI-trade 无持久化 equity 表，去掉 live-auto 版的「最后权益」项，P2-3），避免历史列表「幽灵进行中」。

    ``resume_enabled``（ai-trade-pause-resume §3.7）：
    - ``False``（默认）⇒ **逐字保持既有行为**（running → stopped，一键回滚到本轮之前）；
    - ``True`` ⇒ running 的 ai-trade run 置 ``paused``（**不回填 end_ts、不写 metrics.reconciled**）
      —— 那行「一律置 stopped」的代码正是用户抱怨的那句「再次启动后就得重新启动交易」的实现。
      它只动数据库、不看交易所，所以强杀时若持仓还在，本地已经「结案」而交易所上那个真实仓位
      没人再看它一眼。

    Returns:
        被对账的**条数**（两种模式同口径）。**返回类型必须保持 `int`**（[P1-3]）：改成 dict 会直接
        打破既有用例 `test_ai_auto_history.py::test_reconcile_orphan_ai_trade_runs` 与
        `db/session.py` 的 `%d` 日志。分组信息走独立的只读 `list_paused_ai_trade_group_ids`。
    """
    orphans = (await session.execute(
        select(StrategyRun).where(
            StrategyRun.mode == _AI_TRADE_MODE, StrategyRun.status == "running"))).scalars().all()
    if not orphans:
        return 0
    for run in orphans:
        if resume_enabled:
            run.status = "paused"
            continue
        run.status = "stopped"
        run.end_ts = await _resolve_orphan_end_ts(session, run)
        metrics = _load_config(run.metrics_json)
        metrics["reconciled"] = True
        run.metrics_json = json.dumps(metrics, ensure_ascii=False)
    await session.commit()
    return len(orphans)


async def list_paused_ai_trade_group_ids(session: AsyncSession) -> list[str]:
    """当前处于 ``paused`` 的 ai-trade ``group_id`` 列表（只读、幂等、与上面那个函数解耦）。

    刻意**不塞进** ``reconcile_orphan_ai_trade_runs`` 的返回值（[P1-3]）：那会把一个有既有用例
    钉着的稳定签名改掉，而这里要的只是一次成本近乎为零的只读查询。
    """
    rows = (await session.execute(
        select(StrategyRun.group_id).where(
            StrategyRun.mode == _AI_TRADE_MODE, StrategyRun.status == "paused",
            StrategyRun.group_id.is_not(None)).distinct())).all()
    return [str(row[0]) for row in rows if row[0]]


async def _resolve_orphan_end_ts(session: AsyncSession, run: StrategyRun) -> int:
    """孤儿 run 的 end_ts：max(最后成交 ts, group_started_ts, start_ts)；全空回 0（无 equity 表项）。"""
    last_trade = (await session.execute(
        select(func.max(Trade.ts)).where(Trade.run_id == run.id))).scalar()
    candidates = [int(v) for v in (last_trade, run.group_started_ts, run.start_ts) if v]
    return max(candidates) if candidates else 0
