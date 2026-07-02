"""多模型训练批次 service 编排（weight-multi-model-training §2.2/§2.5）。

批次 = 既有单次训练引擎之上的一层**薄编排**：守门（成员 / bar / 每候选 config / 区间 / 候选数）→ 建批次
父模型（``source="batch"``）+ 批次行 + 每候选一条 ``queued`` 记录 → 注册批次运行时（调度泵 + fan-out）。
候选=与独立 ``/weight-models/train`` 字节级等价的 ``_TrainJob``（绝不派生第二训练器）。择优 / 级联删镜像
既有权重模型路径（``selected_run_id`` 权威、单事务级联删）。取数 / 计算全程真实 OKX，绝不假数据。
"""
from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import repositories as repo
from ..db.models import WeightTrainRun
from ..schemas import (
    WeightBatchStartReq,
    weight_batch_out,
    weight_batch_summary_out,
)
from .service import FactorWindow
from .specs import validate_members
from .weight_batch_manager import get_weight_batch_manager
from .weight_batch_runtime import BatchSharedSpec, CandidateSpec, WeightBatchRuntime
from .weight_model_service import (
    _guard_range,
    _members_summary,
    _validate_bar,
    _validate_train_config,
)
from .weight_train_manager import get_weight_train_manager


def _load_json(raw: str | None, fallback):
    try:
        value = json.loads(raw) if raw else fallback
    except (json.JSONDecodeError, TypeError):
        return fallback
    return value


# ============================ 批次读 ============================
async def list_batches(session: AsyncSession) -> list[dict]:
    """全部批次概要（newest first；含候选计数 + 最优候选摘要 + 派生状态）。"""
    batches = await repo.list_weight_batches(session)
    out: list[dict] = []
    for batch in batches:
        runs = await repo.list_batch_runs(session, batch.id)
        out.append(weight_batch_summary_out(batch, runs))
    return out


async def get_batch(session: AsyncSession, batch_id: int) -> dict | None:
    """批次详情（共享规格 + 候选 run 概要 + ranking + selectedRunId + 派生 status）；不存在 → ``None``。"""
    batch = await repo.get_weight_batch(session, batch_id)
    if batch is None:
        return None
    runs = await repo.list_batch_runs(session, batch_id)
    return weight_batch_out(batch, runs)


# ============================ 批次启动（守门优先 → 建父模型/批次/候选 → 注册运行时） ============================
def _batch_label(name: str | None, members: list[dict]) -> str:
    if name:
        return name
    summary = _members_summary(members)
    return f"{summary} · 多模型对比" if summary else "多模型对比"


async def start_batch(session: AsyncSession, rest, req: WeightBatchStartReq) -> dict:
    """启动多模型训练批次：**守门优先、全过后才落库 / 注册运行时**（绝不留孤儿）。

    守门序：① 成员 400 → ② bar 400 → ③ 每候选 config 400 → ④ 区间 413 → ⑤ 候选数上限 400。
    候选超可用名额时进服务端 FIFO 队列，名额释放即泵起（绝不突破 ``max_weight_train_jobs``）；批次本身**不** 429。

    Returns:
        ``{batchId, parentModelId, label, origin, candidates:[{runId, method, variantLabel, status}]}``。

    Raises:
        HTTPException: 400 成员 / bar / 候选 config / 候选数非法 ｜ 413 区间过长。
    """
    members = validate_members([m.model_dump() for m in req.members])       # ① 成员 400
    _validate_bar(req.bar)                                                  # ② bar 400（先于 413）
    settings = get_settings()
    cap = settings.max_weight_batch_candidates
    if len(req.candidates) > cap:                                           # ⑤ 候选数上限 400
        raise HTTPException(400, detail={
            "code": "WEIGHT_BATCH_TOO_MANY_CANDIDATES",
            "message": f"单批候选数不得超过 {cap} 个，请减少候选后重试。"})
    shared_config = dict(req.config or {})
    merged_configs = [{**shared_config, **(c.config or {})} for c in req.candidates]
    for cfg in merged_configs:
        _validate_train_config(cfg)                                        # ③ 每候选 config 400（先于 413）
    window = FactorWindow(inst_id=req.instId, bar=req.bar, start_ts=req.startTs,
                          end_ts=req.endTs, max_bars=req.maxBars)
    _guard_range(window)                                                    # ④ 区间 413

    # 守门全过 → 建父模型（source="batch"）→ 建批次行 → 逐候选落 queued 记录 → 注册运行时。
    parent = await repo.create_weight_model(session, spec=repo.WeightModelSpec(
        name=_batch_label(req.name, members), inst_id=req.instId, bar=req.bar, members=members,
        method=req.candidates[0].method, config=shared_config, horizon=req.horizon,
        source="batch", factor_set_id=req.factorSetId))
    batch = await repo.create_weight_batch(session, spec=repo.WeightBatchSpec(
        label=_batch_label(req.name, members), inst_id=req.instId, bar=req.bar,
        start_ts=req.startTs, end_ts=req.endTs, horizon=req.horizon, members=members,
        config=shared_config, parent_model_id=parent.id, factor_set_id=req.factorSetId,
        origin=req.origin))
    candidates: list[CandidateSpec] = []
    roster: list[dict] = []
    for cand_in, cfg in zip(req.candidates, merged_configs):
        run = await repo.add_queued_weight_run(session, start=repo.WeightRunStart(
            model_id=parent.id, method=cand_in.method, inst_id=req.instId, bar=req.bar,
            start_ts=req.startTs, end_ts=req.endTs, horizon=req.horizon, config=cfg,
            started_ts=0, batch_id=batch.id, variant_label=cand_in.variantLabel))
        candidates.append(CandidateSpec(run_id=run.id, method=cand_in.method, config=dict(cand_in.config or {}),
                                        variant_label=cand_in.variantLabel))
        roster.append({"runId": run.id, "method": cand_in.method,
                       "variantLabel": cand_in.variantLabel, "status": "queued"})

    runtime = WeightBatchRuntime(
        batch_id=batch.id, parent_model_id=parent.id, rest=rest,
        manager=get_weight_train_manager(),
        spec=BatchSharedSpec(
            inst_id=req.instId, bar=req.bar, start_ts=req.startTs, end_ts=req.endTs,
            max_bars=req.maxBars, horizon=req.horizon, members=members, config=shared_config,
            run_cap=settings.weight_model_run_cap, curve_steps=settings.weight_train_curve_steps),
        candidates=candidates)
    get_weight_batch_manager().register(runtime)
    return {"batchId": batch.id, "parentModelId": parent.id, "label": batch.label,
            "origin": batch.origin, "candidates": roster}


# ============================ 择优 / 取消 / 删除 ============================
async def select_run(session: AsyncSession, batch_id: int, run_id: int) -> dict | None:
    """把某成功候选记为批次显式最优（写 ``selected_run_id``）；批次 / 候选不存在 → ``None``（路由 404）。

    Raises:
        HTTPException: 409 ``WEIGHT_BATCH_RUN_NOT_SELECTABLE``（候选非 succeeded / 空权重 / 不属本批）。
    """
    batch = await repo.get_weight_batch(session, batch_id)
    if batch is None:
        return None
    run = await session.get(WeightTrainRun, run_id)
    if run is None or run.batch_id != batch_id:
        return None
    weights = _load_json(run.weights_json, {})
    if run.status != "succeeded" or not weights:
        raise HTTPException(409, detail={
            "code": "WEIGHT_BATCH_RUN_NOT_SELECTABLE",
            "message": "该候选未成功或未产出权重，无法择优，请改选一个成功候选。"})
    await repo.set_batch_selected_run(session, batch_id, run_id)
    return await get_batch(session, batch_id)


async def cancel_batch(session: AsyncSession, batch_id: int) -> str:
    """整批取消；返回 'cancelling'（有运行时在跑）| 'finished'（已终态 / 已回收，幂等）| 'not_found'。"""
    batch = await repo.get_weight_batch(session, batch_id)
    if batch is None:
        return "not_found"
    ok = await get_weight_batch_manager().cancel_batch(batch_id)
    return "cancelling" if ok else "finished"


async def cancel_candidate(session: AsyncSession, batch_id: int, run_id: int) -> str:
    """取消单候选；返回 'cancelling' | 'finished' | 'not_found'。"""
    batch = await repo.get_weight_batch(session, batch_id)
    if batch is None:
        return "not_found"
    result = await get_weight_batch_manager().cancel_candidate(batch_id, run_id)
    if result == "unknown":                         # 运行时已回收 / 候选已终态 → 按 DB 状态映射幂等
        run = await session.get(WeightTrainRun, run_id)
        if run is None or run.batch_id != batch_id:
            return "not_found"
        return "finished"
    return "cancelling" if result == "cancelling" else "finished"


async def delete_batch(session: AsyncSession, batch_id: int) -> str:
    """删批次（级联删父模型 + 全部候选 + 批次行，单事务，R5）；返回 'ok' | 'not_found' | 'running'。

    仍有 running/queued 候选 → ``"running"``（路由 409，须先取消）。
    """
    result = await repo.cascade_delete_weight_batch(session, batch_id)
    if result == "ok":                                   # DB 行已删 → 同步移除内存运行时，杜绝删后重连回放陈旧缓冲
        get_weight_batch_manager().discard(batch_id)
    return result
