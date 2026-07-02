"""多模型训练批次数据访问层（weight-multi-model-training §3.1，镜像 ``weight_model_repositories.py``）。

批次行 CRUD + 子候选查询 + ``selected_run_id`` 写入 + **service 层单事务级联删**（删候选 run + 父模型 +
批次行，镜像 ``delete_weight_model``，**绝不依赖 DB 外键级联**，R5）。所有公开符号经 ``repositories.py``
re-export，故 ``repo.X`` 调用点统一。``queued`` 候选的落库 / 转 running 复用 ``weight_model_repositories``
的 ``add_queued_weight_run``/``mark_run_running``（不在此重复）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import WeightModel, WeightTrainBatch, WeightTrainRun


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False)


@dataclass(frozen=True)
class WeightBatchSpec:
    """创建一个训练批次的共享规格（聚合传参，避免位置参数堆叠）。"""

    label: str
    inst_id: str
    bar: str
    start_ts: int
    end_ts: int
    horizon: int
    members: list
    config: dict
    parent_model_id: int
    factor_set_id: int | None = None
    origin: str = "model"
    # factor-combo-exploration §2.5：kind 区分多方法批次 / 探索批次；explore_plan 存探索计划快照（多方法批次缺省空）。
    kind: str = "multi_method"
    explore_plan: dict | None = None


async def create_weight_batch(session: AsyncSession, *, spec: WeightBatchSpec) -> WeightTrainBatch:
    """落一行批次并提交（父模型 + 候选记录由 service 在同一编排内先后落库）。"""
    row = WeightTrainBatch(
        label=spec.label, inst_id=spec.inst_id, bar=spec.bar, start_ts=spec.start_ts,
        end_ts=spec.end_ts, horizon=int(spec.horizon), members_json=_dump(spec.members or []),
        config_json=_dump(spec.config or {}), parent_model_id=spec.parent_model_id,
        factor_set_id=spec.factor_set_id, origin=spec.origin,
        kind=spec.kind, explore_plan_json=_dump(spec.explore_plan or {}))
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_weight_batch(session: AsyncSession, batch_id: int) -> WeightTrainBatch | None:
    return await session.get(WeightTrainBatch, batch_id)


async def list_weight_batches(session: AsyncSession) -> list[WeightTrainBatch]:
    """全部批次，按 id 降序（最新在前）。"""
    stmt = select(WeightTrainBatch).order_by(WeightTrainBatch.id.desc())
    return list((await session.execute(stmt)).scalars().all())


async def list_batch_runs(session: AsyncSession, batch_id: int) -> list[WeightTrainRun]:
    """某批次的全部候选训练记录，按 id 升序（与创建顺序 / roster 一致）。"""
    stmt = (select(WeightTrainRun).where(WeightTrainRun.batch_id == batch_id)
            .order_by(WeightTrainRun.id.asc()))
    return list((await session.execute(stmt)).scalars().all())


async def set_batch_selected_run(session: AsyncSession, batch_id: int, run_id: int) -> WeightTrainBatch | None:
    """写批次显式择优候选（``selected_run_id``）并提交；批次不存在 → ``None``（可选性校验由 service 层前置）。"""
    batch = await session.get(WeightTrainBatch, batch_id)
    if batch is None:
        return None
    batch.selected_run_id = run_id
    await session.commit()
    await session.refresh(batch)
    return batch


async def cascade_delete_weight_batch(session: AsyncSession, batch_id: int) -> str:
    """单事务级联删批次（R5）：删父模型全部候选 run + 父模型行 + 批次行；返回 'ok' | 'not_found' | 'running'。

    镜像 ``delete_weight_model`` 的「先删 run 再删 model」口径（本机 SQLite 未开外键约束，不依赖 DB 级联）。
    仍有 ``running``/``queued`` 候选 → 返 ``"running"``（须先取消），杜绝删掉在跑候选留下悬挂作业。
    父模型 ``source="batch"`` 仅承载本批候选，故按 ``parent_model_id`` 收全部候选即完整。
    """
    batch = await session.get(WeightTrainBatch, batch_id)
    if batch is None:
        return "not_found"
    runs = (await session.execute(select(WeightTrainRun).where(
        WeightTrainRun.model_id == batch.parent_model_id))).scalars().all()
    if any(run.status in ("running", "queued") for run in runs):
        return "running"
    for run in runs:
        await session.delete(run)
    model = await session.get(WeightModel, batch.parent_model_id)
    if model is not None:
        await session.delete(model)
    await session.delete(batch)
    await session.commit()
    return "ok"
