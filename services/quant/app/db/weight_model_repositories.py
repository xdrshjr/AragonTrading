"""权重模型 + 训练记录数据访问层（weight-model-workspace §四，镜像 ``factor_repositories.py``）。

模型与记录 CRUD + 状态机收口 + 指针完整性（R-05）+ 启动对账（R-04）。所有公开符号经 ``repositories.py``
顶部 re-export，故 ``repo.X`` 调用点统一（service / api / 测试），私有 ``_*`` 不外露。

R-05（指针完整性）：``latest_run_id`` 为 FK-less 整数（仿 ``LiveOrderAudit.account_id``，规避 model↔run
循环依赖）；任何剪枝 / 删除路径**永不删指针记录**、且变更后 ``repoint_weight_model`` 重算 ``latest_run_id`` 与
``best_valid_ic``（取剩余 succeeded 最近 run / 最大 valid IC，删尽置 ``None``），杜绝悬挂指针 / 陈旧冗余。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import WeightModel, WeightTrainRun

logger = logging.getLogger("aragon")

WEIGHT_MODEL_SOURCES = ("builder", "manual")


# ============================ 聚合传参（frozen dataclass，仿 FactorSetValues） ============================
@dataclass(frozen=True)
class WeightModelSpec:
    """权重模型的训练规格（创建 + 复用刷新共用，避免位置参数堆叠）。"""

    name: str
    inst_id: str
    bar: str
    members: list
    method: str
    config: dict
    horizon: int
    source: str = "manual"
    factor_set_id: int | None = None
    notes: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class WeightModelPatch:
    """编辑权重模型的部分字段（``None`` = 不改）。"""

    name: str | None = None
    inst_id: str | None = None
    bar: str | None = None
    members: list | None = None
    method: str | None = None
    config: dict | None = None
    horizon: int | None = None
    notes: str | None = None
    enabled: bool | None = None
    factor_set_id: int | None = None


@dataclass(frozen=True)
class WeightRunStart:
    """启动一次训练记录的快照入参（落 ``running`` 记录）。"""

    model_id: int
    method: str
    inst_id: str
    bar: str
    start_ts: int
    end_ts: int
    horizon: int
    config: dict
    started_ts: int


@dataclass(frozen=True)
class WeightRunOutcome:
    """训练记录终态产物（succeeded / failed / cancelled 共用一套装配）。"""

    status: str
    finished_ts: int
    curve: list = field(default_factory=list)
    weights: dict = field(default_factory=dict)
    norm_stats: dict = field(default_factory=dict)
    metrics: dict | None = None
    provenance: dict = field(default_factory=dict)
    trainer_version: str | None = None
    note: str = ""
    error_code: str = ""
    error_message: str = ""


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _valid_ic_of(run: WeightTrainRun) -> float | None:
    """某 run 的「验证 IC」= ``metrics.icOut``（样本外复合得分 IC，与 UI「样本外 IC」同口径）。"""
    try:
        metrics = json.loads(run.metrics_json or "")
    except (json.JSONDecodeError, TypeError):
        return None
    value = metrics.get("icOut") if isinstance(metrics, dict) else None
    return float(value) if isinstance(value, (int, float)) else None


# ============================ 模型 CRUD ============================
async def list_weight_models(session: AsyncSession, *, enabled: bool | None = None) -> list[WeightModel]:
    stmt = select(WeightModel).order_by(WeightModel.id.asc())
    if enabled is not None:
        stmt = stmt.where(WeightModel.enabled == enabled)
    return list((await session.execute(stmt)).scalars().all())


async def get_weight_model(session: AsyncSession, model_id: int) -> WeightModel | None:
    return await session.get(WeightModel, model_id)


def _model_row(spec: WeightModelSpec) -> WeightModel:
    return WeightModel(
        name=spec.name, inst_id=spec.inst_id, bar=spec.bar,
        members_json=_dump(spec.members or []), method=spec.method,
        config_json=_dump(spec.config or {}), horizon=int(spec.horizon),
        source=spec.source, factor_set_id=spec.factor_set_id,
        notes=spec.notes or "", enabled=spec.enabled)


async def create_weight_model(session: AsyncSession, *, spec: WeightModelSpec) -> WeightModel:
    row = _model_row(spec)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


def _apply_spec_refresh(row: WeightModel, spec: WeightModelSpec) -> None:
    """复用模型时仅刷新训练规格（成员 / 方法 / 配置 / 标的 / 周期 / horizon / 来源链接），保留用户 name/notes/enabled。"""
    row.inst_id = spec.inst_id
    row.bar = spec.bar
    row.members_json = _dump(spec.members or [])
    row.method = spec.method
    row.config_json = _dump(spec.config or {})
    row.horizon = int(spec.horizon)
    if spec.factor_set_id is not None:
        row.factor_set_id = spec.factor_set_id


async def refresh_weight_model_spec(session: AsyncSession, row: WeightModel, *,
                                    spec: WeightModelSpec) -> WeightModel:
    """以最新训练入参刷新已存模型的 spec（复用语义，R-02）并提交。"""
    _apply_spec_refresh(row, spec)
    await session.commit()
    await session.refresh(row)
    return row


async def find_weight_model_by_factor_set(session: AsyncSession, factor_set_id: int) -> WeightModel | None:
    """按弱关联 ``factor_set_id`` 取最小 id 的模型（同组合不重复造模型，R-02）。"""
    stmt = (select(WeightModel).where(WeightModel.factor_set_id == factor_set_id)
            .order_by(WeightModel.id.asc()).limit(1))
    return (await session.execute(stmt)).scalars().first()


async def update_weight_model(session: AsyncSession, model_id: int, *,
                              patch: WeightModelPatch) -> WeightModel | None:
    row = await session.get(WeightModel, model_id)
    if row is None:
        return None
    if patch.name is not None:
        row.name = patch.name
    if patch.inst_id is not None:
        row.inst_id = patch.inst_id
    if patch.bar is not None:
        row.bar = patch.bar
    if patch.members is not None:
        row.members_json = _dump(patch.members)
    if patch.method is not None:
        row.method = patch.method
    if patch.config is not None:
        row.config_json = _dump(patch.config)
    if patch.horizon is not None:
        row.horizon = int(patch.horizon)
    if patch.notes is not None:
        row.notes = patch.notes
    if patch.enabled is not None:
        row.enabled = patch.enabled
    if patch.factor_set_id is not None:
        row.factor_set_id = patch.factor_set_id
    await session.commit()
    await session.refresh(row)
    return row


async def delete_weight_model(session: AsyncSession, model_id: int) -> str:
    """删模型并**连带删其全部训练记录**（单事务）；返回 'ok' | 'not_found'。

    本机 SQLite 未开外键约束，先删 run 行再删 model 行与 ``factor_score`` 删除同口径，不因 FK 失败。
    """
    row = await session.get(WeightModel, model_id)
    if row is None:
        return "not_found"
    runs = (await session.execute(
        select(WeightTrainRun).where(WeightTrainRun.model_id == model_id))).scalars().all()
    for run in runs:
        await session.delete(run)
    await session.delete(row)
    await session.commit()
    return "ok"


# ============================ 训练记录 + 指针完整性（R-05） ============================
async def add_weight_run(session: AsyncSession, *, start: WeightRunStart) -> WeightTrainRun:
    """落一条 ``running`` 训练记录并提交（注册作业前持久化，R-01）。"""
    row = WeightTrainRun(
        model_id=start.model_id, status="running", method=start.method,
        inst_id=start.inst_id, bar=start.bar, start_ts=start.start_ts, end_ts=start.end_ts,
        horizon=int(start.horizon), config_json=_dump(start.config or {}),
        started_ts=start.started_ts)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_weight_run(session: AsyncSession, model_id: int, run_id: int) -> WeightTrainRun | None:
    row = await session.get(WeightTrainRun, run_id)
    return row if row is not None and row.model_id == model_id else None


async def list_weight_runs(session: AsyncSession, model_id: int, *, limit: int | None = None,
                    status: str | None = None) -> list[WeightTrainRun]:
    stmt = select(WeightTrainRun).where(WeightTrainRun.model_id == model_id)
    if status:
        stmt = stmt.where(WeightTrainRun.status == status)
    stmt = stmt.order_by(WeightTrainRun.id.desc())
    if limit is not None:
        stmt = stmt.limit(int(limit))
    return list((await session.execute(stmt)).scalars().all())


async def repoint_weight_model(session: AsyncSession, model: WeightModel) -> None:
    """据剩余 succeeded 记录重算 ``latest_run_id``（最大 id）与 ``best_valid_ic``（最大 icOut）（flush，不 commit）。

    无 succeeded 记录则双双置 ``None``（删尽不留悬挂指针 / 陈旧冗余，R-05）。
    """
    rows = (await session.execute(
        select(WeightTrainRun).where(WeightTrainRun.model_id == model.id,
                                     WeightTrainRun.status == "succeeded"))).scalars().all()
    if not rows:
        model.latest_run_id = None
        model.best_valid_ic = None
    else:
        model.latest_run_id = max(r.id for r in rows)
        ics = [v for v in (_valid_ic_of(r) for r in rows) if v is not None]
        model.best_valid_ic = max(ics) if ics else None
    await session.flush()


async def _prune_runs(session: AsyncSession, model: WeightModel, cap: int) -> int:
    """删超出 ``cap`` 的最旧记录，**永不删 ``latest_run_id`` 指向或 running 记录**（R-05）；返回丢弃数并显式 log。"""
    rows = (await session.execute(
        select(WeightTrainRun).where(WeightTrainRun.model_id == model.id)
        .order_by(WeightTrainRun.id.desc()))).scalars().all()
    dropped = 0
    for idx, run in enumerate(rows):
        if idx < cap or run.id == model.latest_run_id or run.status == "running":
            continue
        await session.delete(run)
        dropped += 1
    if dropped:
        logger.info("weight model %d pruned %d old training runs (cap=%d)", model.id, dropped, cap)
    return dropped


async def finalize_weight_run(session: AsyncSession, run_id: int, *, outcome: WeightRunOutcome,
                       run_cap: int) -> WeightTrainRun | None:
    """把训练记录收口到终态并维护模型指针 / 剪枝（单事务，R-04/R-05）；run 不存在 → ``None``。

    顺序：写 run 终态 → ``repoint_weight_model``（指针指向含本条的最近 succeeded）→ 剪枝（跳过指针 / running）
    → 剪枝有删除则再 ``repoint_weight_model`` 重算 ``best_valid_ic`` → **单次 commit**。
    """
    run = await session.get(WeightTrainRun, run_id)
    if run is None:
        return None
    run.status = outcome.status
    run.curve_json = _dump(outcome.curve)
    run.weights_json = _dump(outcome.weights)
    run.norm_stats_json = _dump(outcome.norm_stats)
    run.metrics_json = _dump(outcome.metrics) if outcome.metrics is not None else None
    run.provenance_json = _dump(outcome.provenance)
    run.trainer_version = outcome.trainer_version
    run.note = outcome.note
    run.error_code = outcome.error_code
    run.error_message = outcome.error_message
    run.finished_ts = outcome.finished_ts
    model = await session.get(WeightModel, run.model_id)
    if model is not None:
        await repoint_weight_model(session, model)
        if await _prune_runs(session, model, run_cap):
            await repoint_weight_model(session, model)
    await session.commit()
    await session.refresh(run)
    return run


async def delete_weight_run(session: AsyncSession, model_id: int, run_id: int) -> str:
    """删一条训练记录；返回 'ok' | 'not_found' | 'running'（running 拒删，409）。

    R-05：删后若该 run 曾被指针指向 / 贡献 ``best_valid_ic``，单事务内 ``repoint_weight_model`` 重算后再 commit。
    """
    run = await session.get(WeightTrainRun, run_id)
    if run is None or run.model_id != model_id:
        return "not_found"
    if run.status == "running":
        return "running"
    await session.delete(run)
    model = await session.get(WeightModel, model_id)
    if model is not None:
        await repoint_weight_model(session, model)
    await session.commit()
    return "ok"


async def reconcile_dangling_weight_runs(session: AsyncSession) -> int:
    """启动对账（R-04）：上轮进程残留的 ``running`` 记录批量改判 ``failed``，并按 R-05 修受影响模型指针。

    仿 ``factors/service.backfill_companions`` 一次性 session；返回改判条数（lifespan 启动调用，幂等）。
    """
    rows = (await session.execute(
        select(WeightTrainRun).where(WeightTrainRun.status == "running"))).scalars().all()
    if not rows:
        return 0
    affected: set[int] = set()
    for run in rows:
        run.status = "failed"
        run.error_code = "WEIGHT_TRAIN_INTERRUPTED"
        run.error_message = "训练在上一次进程退出时被中断（启动对账改判）。"
        affected.add(run.model_id)
    for model_id in affected:
        model = await session.get(WeightModel, model_id)
        if model is not None:
            await repoint_weight_model(session, model)
    await session.commit()
    return len(rows)
