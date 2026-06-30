"""权重模型 / 训练记录 service 编排（weight-model-workspace §3.3）。

模型 CRUD 校验编排 + spec 组装 + 训练启动守门（**守门优先、全过后才持久化**，R-01）与模型去重解析
（R-02）。流式作业归 ``weight_train_manager``（manager 只持注册表，作业续跑与 socket 解耦）。取数 / 计算
全程真实 OKX，绝不假数据、绝不静默降级。
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import repositories as repo
from ..db.models import WeightModel
from ..engine.factors.scoring import DEFAULT_CLIP, DEFAULT_NORM_WINDOW
from ..engine.mtf import bar_to_seconds
from ..schemas import (
    WeightModelCreateReq,
    WeightModelUpdateReq,
    WeightTrainStartReq,
    weight_model_out,
    weight_train_run_out,
)
from .service import FactorWindow, _load_df  # noqa: F401  —— 复用进程内 K 线缓存（注册表，间接经 manager）
from .specs import validate_members
from .weight_train_manager import JobSpec, get_weight_train_manager


# ============================ 模型 CRUD ============================
async def _model_out(session: AsyncSession, row: WeightModel) -> dict:
    """模型行 → 前端 dict，并按 ``latest_run_id`` 附最近成功训练记录概要（无则 ``None``）。"""
    latest = None
    if row.latest_run_id is not None:
        latest = await repo.get_weight_run(session, row.id, row.latest_run_id)
    return weight_model_out(row, latest_run=latest)


async def list_models(session: AsyncSession, *, enabled: bool | None = None) -> list[dict]:
    rows = await repo.list_weight_models(session, enabled=enabled)
    return [await _model_out(session, row) for row in rows]


async def get_model(session: AsyncSession, model_id: int) -> dict | None:
    row = await repo.get_weight_model(session, model_id)
    return await _model_out(session, row) if row is not None else None


async def create_model(session: AsyncSession, req: WeightModelCreateReq) -> dict:
    """手动新建模型（编辑器）：成员边界校验（400）→ 落库 → 序列化。"""
    members = validate_members([m.model_dump() for m in req.members])
    spec = repo.WeightModelSpec(
        name=req.name, inst_id=req.instId, bar=req.bar, members=members, method=req.method,
        config=req.config, horizon=req.horizon, source="manual",
        factor_set_id=req.factorSetId, notes=req.notes, enabled=req.enabled)
    row = await repo.create_weight_model(session, spec=spec)
    return await _model_out(session, row)


async def update_model(session: AsyncSession, model_id: int, req: WeightModelUpdateReq) -> dict | None:
    members = validate_members([m.model_dump() for m in req.members]) if req.members is not None else None
    patch = repo.WeightModelPatch(
        name=req.name, inst_id=req.instId, bar=req.bar, members=members, method=req.method,
        config=req.config, horizon=req.horizon, notes=req.notes, enabled=req.enabled)
    row = await repo.update_weight_model(session, model_id, patch=patch)
    return await _model_out(session, row) if row is not None else None


async def delete_model(session: AsyncSession, model_id: int) -> str:
    return await repo.delete_weight_model(session, model_id)


# ============================ 训练记录读 / 删 ============================
async def list_runs(session: AsyncSession, model_id: int, *, limit: int | None,
                    status: str | None) -> list[dict] | None:
    """某模型的训练记录列表（不含完整 curve，仅 ``curveCount``）；模型不存在 → ``None``（路由转 404）。"""
    if await repo.get_weight_model(session, model_id) is None:
        return None
    rows = await repo.list_weight_runs(session, model_id, limit=limit, status=status)
    return [weight_train_run_out(row, with_curve=False) for row in rows]


async def get_run(session: AsyncSession, model_id: int, run_id: int) -> dict | None:
    """某条训练记录详情（含完整 curve / weights / metrics / provenance）；不存在 → ``None``。"""
    row = await repo.get_weight_run(session, model_id, run_id)
    return weight_train_run_out(row, with_curve=True) if row is not None else None


async def delete_run(session: AsyncSession, model_id: int, run_id: int) -> str:
    return await repo.delete_weight_run(session, model_id, run_id)


# ============================ 训练启动（守门优先 → 去重解析 → 落库 → 注册作业，R-01/R-02） ============================
def _guard_range(window: FactorWindow) -> None:
    """区间有效根数超 ``factor_train_max_bars`` → 413（复用同步训练护栏；绝不让无界长请求拖垮服务）。"""
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


def _members_summary(members: list[dict]) -> str:
    """缺省模型名 = 成员摘要（前若干因子 key 拼接），便于列表识别。"""
    keys = [str(m.get("factorKey", "")) for m in members if m.get("factorKey")]
    head = "·".join(keys[:3])
    suffix = f" 等{len(keys)}因子" if len(keys) > 3 else ""
    return f"{head}{suffix} 权重模型" if head else "权重模型"


async def _resolve_model(session: AsyncSession, req: WeightTrainStartReq,
                         members: list[dict]) -> WeightModel:
    """模型去重解析（R-02）：modelId 复用 → factorSetId 复用-或-建 → 否则按 name/成员摘要新建。"""
    spec = repo.WeightModelSpec(
        name=req.name or _members_summary(members), inst_id=req.instId, bar=req.bar,
        members=members, method=req.method, config=req.config, horizon=req.horizon,
        source="builder", factor_set_id=req.factorSetId)
    if req.modelId is not None:
        existing = await repo.get_weight_model(session, req.modelId)
        if existing is not None:
            return await repo.refresh_weight_model_spec(session, existing, spec=spec)
    if req.factorSetId is not None:
        existing = await repo.find_weight_model_by_factor_set(session, req.factorSetId)
        if existing is not None:
            return await repo.refresh_weight_model_spec(session, existing, spec=spec)
    return await repo.create_weight_model(session, spec=spec)


async def start_training(session: AsyncSession, rest, req: WeightTrainStartReq) -> dict:
    """启动流式训练：**守门优先（成员 400 → 区间 413 → 并发 429），全过后才落库 / 注册作业**（R-01，杜绝孤儿）。

    Returns:
        ``{jobId, runId, modelId}``。

    Raises:
        HTTPException: 400 成员非法 / 413 区间过长 / 429 作业繁忙。
    """
    members = validate_members([m.model_dump() for m in req.members])   # ① 400
    window = FactorWindow(inst_id=req.instId, bar=req.bar, start_ts=req.startTs,
                          end_ts=req.endTs, max_bars=req.maxBars)
    _guard_range(window)                                                # ② 413
    settings = get_settings()
    manager = get_weight_train_manager()
    if manager.active_count() >= settings.max_weight_train_jobs:        # ③ 429
        raise HTTPException(429, detail={
            "code": "WEIGHT_TRAIN_BUSY",
            "message": f"并发训练作业已达上限 {settings.max_weight_train_jobs}，请稍后重试。"})
    # 守门全过 → 解析 / 建模型（R-02）→ 落 running 记录 → 注册作业（此刻起即开缓冲，R-06）。
    model = await _resolve_model(session, req, members)
    run = await repo.add_weight_run(session, start=repo.WeightRunStart(
        model_id=model.id, method=req.method, inst_id=req.instId, bar=req.bar,
        start_ts=req.startTs, end_ts=req.endTs, horizon=req.horizon, config=req.config,
        started_ts=_now()))
    job = manager.start_job(JobSpec(
        run_id=run.id, model_id=model.id, rest=rest, members=members, method=req.method,
        config=req.config, window=window, horizon=req.horizon,
        run_cap=settings.weight_model_run_cap, curve_steps=settings.weight_train_curve_steps))
    return {"jobId": job.job_id, "runId": run.id, "modelId": model.id}


def cancel_training(job_id: str) -> dict:
    """协作式取消训练作业（R-03）；返回管理器解析状态供路由映射 202/200/404。"""
    return {"jobId": job_id, "result": get_weight_train_manager().cancel(job_id)}


def _now() -> int:
    import time
    return int(time.time())
