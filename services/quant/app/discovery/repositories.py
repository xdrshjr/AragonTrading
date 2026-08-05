"""挖掘三表的数据访问（strategy-factor-discovery §5 / §2.10）。

承重：``commit_iteration`` 是**一个** DB 事务（候选 + 迭代 + 检查点同写同回滚）。任意时点被杀
都只会丢「正在进行的那一轮」，已完成的轮次绝不半写——这是 24/7 长跑「断点续跑不落空」的物理
保证，也是 D-3 把已测哈希放 DB 唯一索引的原因。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import DiscoveryCandidate, DiscoveryIteration, DiscoveryRun

TERMINAL_STATUSES = ("stopped", "succeeded", "failed")
ACTIVE_STATUSES = ("running", "paused")


@dataclass(frozen=True)
class RunValues:
    """新建挖掘任务的可写字段（聚合传参，避免位置参数堆叠）。"""

    label: str
    inst_id: str
    bar: str
    symbols: list[str]
    config: dict
    checkpoint: dict
    started_ts: int


@dataclass
class CandidateRow:
    """一条待落库的候选（内存形态 → 行；``metrics`` / ``series`` 由验证层给出）。"""

    kind: str
    genome_hash: str
    genome: dict
    expr_text: str
    describe_text: str
    skeleton: str
    mechanism: str
    source: str
    verdict: str
    reject_reason: str = ""
    metrics: dict = field(default_factory=dict)
    series: dict = field(default_factory=dict)
    trials_at_accept: int = 0
    created_ts: int = 0


@dataclass
class IterationRow:
    """一条待落库的迭代记录。"""

    idx: int
    started_ts: int
    finished_ts: int
    budget: dict
    stats: dict
    llm: dict
    note: str = ""


def _dump(value: object) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


# ============================ run CRUD ============================
async def create_run(session: AsyncSession, *, values: RunValues) -> DiscoveryRun:
    row = DiscoveryRun(
        label=values.label, inst_id=values.inst_id, bar=values.bar,
        symbols_json=_dump(values.symbols), status="running", pause_reason="",
        config_json=_dump(values.config), checkpoint_json=_dump(values.checkpoint),
        counters_json=_dump({"tested": 0, "kept": 0, "rejected": {}}),
        iteration=0, kept_count=0, tested_count=0, started_ts=int(values.started_ts))
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_run(session: AsyncSession, run_id: int) -> DiscoveryRun | None:
    return await session.get(DiscoveryRun, run_id)


async def list_runs(session: AsyncSession, *, status: str | None = None,
                    limit: int = 50, offset: int = 0) -> tuple[list[DiscoveryRun], int]:
    """任务列表（倒序）+ 总数（分页真源；绝不用 len(items) 冒充 total）。"""
    stmt = select(DiscoveryRun)
    count_stmt = select(func.count()).select_from(DiscoveryRun)
    if status:
        stmt = stmt.where(DiscoveryRun.status == status)
        count_stmt = count_stmt.where(DiscoveryRun.status == status)
    stmt = stmt.order_by(DiscoveryRun.id.desc()).limit(int(limit)).offset(int(offset))
    rows = list((await session.execute(stmt)).scalars().all())
    total = int((await session.execute(count_stmt)).scalar() or 0)
    return rows, total


async def set_run_status(session: AsyncSession, run_id: int, *, status: str,
                         pause_reason: str = "", finished_ts: int | None = None,
                         error_code: str = "", error_message: str = "") -> DiscoveryRun | None:
    """置状态（含终态）；``pause_reason`` 如实区分暂停原因，UI 据此给不同的下一步引导。"""
    row = await session.get(DiscoveryRun, run_id)
    if row is None:
        return None
    row.status = status
    row.pause_reason = pause_reason
    if finished_ts is not None:
        row.finished_ts = int(finished_ts)
    if error_code:
        row.error_code = error_code[:64]
        row.error_message = (error_message or "")[:256]
    await session.commit()
    await session.refresh(row)
    return row


async def delete_run(session: AsyncSession, run_id: int) -> str:
    """删任务并级联删迭代与候选（service 层单事务保证，绝不依赖 DB 级联）。"""
    row = await session.get(DiscoveryRun, run_id)
    if row is None:
        return "not_found"
    await session.execute(delete(DiscoveryCandidate).where(
        DiscoveryCandidate.run_id == run_id))
    await session.execute(delete(DiscoveryIteration).where(
        DiscoveryIteration.run_id == run_id))
    await session.delete(row)
    await session.commit()
    return "deleted"


async def reconcile_orphan_runs(session: AsyncSession, *, paused_reason: str) -> list[int]:
    """启动对账：把上轮进程残留的 ``running`` 行一律置 ``paused``（绝不留幽灵进行中）。

    进程启动时内存管理器为空，任何仍标 ``running`` 的 run 必是上次崩溃 / 强杀遗留。返回被
    对账的 run id 列表，供调用方按 ``auto_resume`` 与总闸决定是否拉起（§2.1 纪律 4）。

    **必须对同一进程内的重复调用幂等地返回同一份名单**：启动路径上它被调两次（``init_db``
    的对账 + lifespan 的自动续跑）。若只认 ``status=="running"``，第二次一律空手而归——
    ``DISCOVERY_AUTO_RESUME`` 会变成一个永远不生效的开关，且不报任何错。故选取条件为
    「仍 ``running``」**或**「已被本轮对账置成 ``paused`` 且原因同为 ``paused_reason``」。
    """
    rows = list((await session.execute(select(DiscoveryRun).where(
        (DiscoveryRun.status == "running")
        | ((DiscoveryRun.status == "paused")
           & (DiscoveryRun.pause_reason == paused_reason))))).scalars().all())
    changed = [row for row in rows if row.status == "running"]
    for row in changed:
        row.status = "paused"
        row.pause_reason = paused_reason
    if changed:
        await session.commit()
    return [int(row.id) for row in rows]


# ============================ 已测哈希 / kept 池（续跑） ============================
async def load_tested_hashes(session: AsyncSession, run_id: int) -> set[str]:
    """一次性载入本 run 的全部已测哈希（D-3：10 万条约 3 MB，可接受）。"""
    rows = (await session.execute(select(DiscoveryCandidate.genome_hash).where(
        DiscoveryCandidate.run_id == run_id))).scalars().all()
    return {str(value) for value in rows}


async def list_kept_candidates(session: AsyncSession, run_id: int, *,
                               include_demoted: bool = False,
                               kind: str = "") -> list[DiscoveryCandidate]:
    """本 run 的已入库候选（默认排除已降级者——它们已退出父本池与去重基准）。"""
    stmt = select(DiscoveryCandidate).where(
        DiscoveryCandidate.run_id == run_id, DiscoveryCandidate.verdict == "kept")
    if not include_demoted:
        stmt = stmt.where(DiscoveryCandidate.demoted == False)  # noqa: E712
    if kind:
        stmt = stmt.where(DiscoveryCandidate.kind == kind)
    return list((await session.execute(stmt.order_by(DiscoveryCandidate.id))).scalars().all())


async def get_candidate(session: AsyncSession, candidate_id: int) -> DiscoveryCandidate | None:
    return await session.get(DiscoveryCandidate, candidate_id)


async def list_candidates(session: AsyncSession, run_id: int, *, verdict: str | None = "kept",
                          mechanism: str = "", reason: str = "", sort: str = "",
                          kind: str = "", limit: int = 50, offset: int = 0
                          ) -> tuple[list[DiscoveryCandidate], int]:
    """候选分页（默认只回 ``kept``；``verdict=""`` 即不过滤；``kind=""`` 即全部轴）。"""
    stmt = select(DiscoveryCandidate).where(DiscoveryCandidate.run_id == run_id)
    count_stmt = select(func.count()).select_from(DiscoveryCandidate).where(
        DiscoveryCandidate.run_id == run_id)
    for column, value in ((DiscoveryCandidate.verdict, verdict),
                          (DiscoveryCandidate.mechanism, mechanism),
                          (DiscoveryCandidate.kind, kind),
                          (DiscoveryCandidate.reject_reason, reason)):
        if value:
            stmt = stmt.where(column == value)
            count_stmt = count_stmt.where(column == value)
    order = (DiscoveryCandidate.trials_at_accept.desc() if sort == "trials"
             else DiscoveryCandidate.id.desc())
    stmt = stmt.order_by(order).limit(int(limit)).offset(int(offset))
    rows = list((await session.execute(stmt)).scalars().all())
    total = int((await session.execute(count_stmt)).scalar() or 0)
    return rows, total


async def mark_demoted(session: AsyncSession, candidate_ids: list[int]) -> int:
    """复评降级：保留记录，退出父本池与去重基准，UI 显示「已退化」徽标。"""
    if not candidate_ids:
        return 0
    rows = list((await session.execute(select(DiscoveryCandidate).where(
        DiscoveryCandidate.id.in_(candidate_ids)))).scalars().all())
    for row in rows:
        row.demoted = True
    await session.commit()
    return len(rows)


async def mark_promoted(session: AsyncSession, candidate_id: int, *, kind: str,
                        entity_id: int) -> DiscoveryCandidate | None:
    row = await session.get(DiscoveryCandidate, candidate_id)
    if row is None:
        return None
    row.promoted_kind = kind[:16]
    row.promoted_id = int(entity_id)
    await session.commit()
    await session.refresh(row)
    return row


# ============================ 单事务提交（承重） ============================
async def commit_iteration(session: AsyncSession, run_id: int, *,
                           candidates: list[CandidateRow], iteration: IterationRow,
                           checkpoint_json: str, counters: dict,
                           candidate_row_cap: int, iteration_row_cap: int) -> dict:
    """**一个事务**写「候选 N 条 + 迭代 1 条 + run 的检查点与计数」，再做保留策略剪枝。

    Args:
        candidate_row_cap: 每 run 保留的 ``rejected`` 候选上限（``kept`` 永不剪）。
        iteration_row_cap: 每 run 保留的迭代记录上限。

    Returns:
        ``{"prunedCandidates": n, "prunedIterations": m}``——剪枝量如实上报，
        由调用方累计进 ``counters``，**绝不静默截断**（R-4）。
    """
    run = await session.get(DiscoveryRun, run_id)
    if run is None:
        raise ValueError(f"discovery run {run_id} 不存在")
    for row in candidates:
        session.add(DiscoveryCandidate(
            run_id=run_id, iteration_idx=int(iteration.idx), kind=row.kind,
            genome_hash=row.genome_hash, genome_json=_dump(row.genome),
            expr_text=row.expr_text, describe_text=row.describe_text[:512],
            skeleton=row.skeleton[:256], mechanism=row.mechanism[:48], source=row.source,
            verdict=row.verdict, reject_reason=row.reject_reason[:64],
            metrics_json=_dump(row.metrics), series_json=_dump(row.series),
            trials_at_accept=int(row.trials_at_accept), demoted=False,
            created_ts=int(row.created_ts)))
    session.add(DiscoveryIteration(
        run_id=run_id, idx=int(iteration.idx), started_ts=int(iteration.started_ts),
        finished_ts=int(iteration.finished_ts), budget_json=_dump(iteration.budget),
        stats_json=_dump(iteration.stats), llm_json=_dump(iteration.llm),
        note=(iteration.note or "")[:256]))
    run.iteration = int(iteration.idx)
    run.checkpoint_json = checkpoint_json
    run.counters_json = _dump(counters)
    run.kept_count = int(counters.get("kept", run.kept_count))
    run.tested_count = int(counters.get("tested", run.tested_count))
    run.last_iter_ts = int(iteration.finished_ts)
    await session.flush()
    pruned = await _prune(session, run_id, candidate_row_cap, iteration_row_cap)
    await session.commit()
    return pruned


async def _prune(session: AsyncSession, run_id: int, candidate_cap: int,
                 iteration_cap: int) -> dict:
    """保留策略：``kept`` 永不剪；``rejected`` 与迭代记录各自剪最旧（剪枝量如实返回）。"""
    pruned_candidates = await _prune_rejected(session, run_id, candidate_cap)
    pruned_iterations = await _prune_iterations(session, run_id, iteration_cap)
    return {"prunedCandidates": pruned_candidates, "prunedIterations": pruned_iterations}


async def _prune_rejected(session: AsyncSession, run_id: int, cap: int) -> int:
    total = int((await session.execute(
        select(func.count()).select_from(DiscoveryCandidate).where(
            DiscoveryCandidate.run_id == run_id,
            DiscoveryCandidate.verdict == "rejected"))).scalar() or 0)
    excess = total - int(cap)
    if excess <= 0:
        return 0
    victims = (await session.execute(
        select(DiscoveryCandidate.id).where(
            DiscoveryCandidate.run_id == run_id,
            DiscoveryCandidate.verdict == "rejected"
        ).order_by(DiscoveryCandidate.id.asc()).limit(excess))).scalars().all()
    if victims:
        await session.execute(delete(DiscoveryCandidate).where(
            DiscoveryCandidate.id.in_(list(victims))))
    return len(victims)


async def _prune_iterations(session: AsyncSession, run_id: int, cap: int) -> int:
    total = int((await session.execute(
        select(func.count()).select_from(DiscoveryIteration).where(
            DiscoveryIteration.run_id == run_id))).scalar() or 0)
    excess = total - int(cap)
    if excess <= 0:
        return 0
    victims = (await session.execute(
        select(DiscoveryIteration.id).where(DiscoveryIteration.run_id == run_id)
        .order_by(DiscoveryIteration.id.asc()).limit(excess))).scalars().all()
    if victims:
        await session.execute(delete(DiscoveryIteration).where(
            DiscoveryIteration.id.in_(list(victims))))
    return len(victims)


async def list_iterations(session: AsyncSession, run_id: int,
                          limit: int = 20) -> list[DiscoveryIteration]:
    """最近 N 轮迭代记录（倒序取、正序回，供详情页时间线直接渲染）。"""
    rows = list((await session.execute(
        select(DiscoveryIteration).where(DiscoveryIteration.run_id == run_id)
        .order_by(DiscoveryIteration.idx.desc()).limit(int(limit)))).scalars().all())
    return list(reversed(rows))


async def update_checkpoint(session: AsyncSession, run_id: int, *, checkpoint_json: str,
                            counters: dict) -> None:
    """只更新检查点与计数（重建 / 复评后落盘，不涉及候选与迭代）。"""
    run = await session.get(DiscoveryRun, run_id)
    if run is None:
        return
    run.checkpoint_json = checkpoint_json
    run.counters_json = _dump(counters)
    await session.commit()
