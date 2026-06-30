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
    """手动新建模型（编辑器）：成员边界校验（400）→ bar 校验（400，H-B5）→ 落库 → 序列化。"""
    members = validate_members([m.model_dump() for m in req.members])
    _validate_bar(req.bar)
    spec = repo.WeightModelSpec(
        name=req.name, inst_id=req.instId, bar=req.bar, members=members, method=req.method,
        config=req.config, horizon=req.horizon, source="manual",
        factor_set_id=req.factorSetId, notes=req.notes, enabled=req.enabled)
    row = await repo.create_weight_model(session, spec=spec)
    return await _model_out(session, row)


async def update_model(session: AsyncSession, model_id: int, req: WeightModelUpdateReq) -> dict | None:
    members = validate_members([m.model_dump() for m in req.members]) if req.members is not None else None
    if req.bar is not None:
        _validate_bar(req.bar)   # H-B5：手动改模型亦校验 bar 白名单
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
def _validate_bar(bar: str) -> None:
    """非白名单周期 → 400（H-B5）：``bar_to_seconds`` 对未知 bar 抛裸 ``ValueError``，在请求边界映射为 400，
    避免漏到 ``_guard_range``（自身调 ``bar_to_seconds``）里抛出无处理器的 500。"""
    try:
        bar_to_seconds(bar)
    except ValueError:
        raise HTTPException(400, detail={
            "code": "FACTOR_INVALID_BAR", "message": f"不支持的周期：{bar}"})


def _config_num(config: dict, key: str) -> float | None:
    """取 config 中数值键（缺省返回 None=合法、引擎用默认）；非数值 → 400（H-B5/R-01）。"""
    if key not in config:
        return None
    try:
        return float(config[key])
    except (TypeError, ValueError):
        raise HTTPException(400, detail={
            "code": "FACTOR_TRAIN_INVALID_CONFIG",
            "message": f"参数 {key} 必须为数值，收到：{config[key]!r}"})


def _validate_train_config(config: dict) -> None:
    """边界校验 config 中**真实且出现**的 camelCase 键（H-B5/R-01）：仅 ``split∈(0,1)`` / ``l2≥0`` /
    ``nEstimators``·``maxDepth`` 正整数；**不**校验 ``clip``（``clip≤0`` 是关闭裁剪的合法哨兵）、**不**要求
    ``normWindow``（``causal_zscore`` 内已 ``max(_,2)`` 钳制）。全部可选、缺省合法。"""
    split = _config_num(config, "split")
    if split is not None and not (0.0 < split < 1.0):
        raise HTTPException(400, detail={
            "code": "FACTOR_TRAIN_INVALID_CONFIG", "message": f"split 须在 (0,1) 内，收到：{split}"})
    l2 = _config_num(config, "l2")
    if l2 is not None and l2 < 0.0:
        raise HTTPException(400, detail={
            "code": "FACTOR_TRAIN_INVALID_CONFIG", "message": f"l2 须 ≥0，收到：{l2}"})
    for key in ("nEstimators", "maxDepth"):     # 可选：present 时须为正整数（越界仍由引擎钳制兜底）
        val = _config_num(config, key)
        if val is not None and (val != int(val) or val <= 0):
            raise HTTPException(400, detail={
                "code": "FACTOR_TRAIN_INVALID_CONFIG",
                "message": f"{key} 须为正整数，收到：{config[key]!r}"})


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
    """启动流式训练：**守门优先、全过后才落库 / 注册作业**（R-01，杜绝孤儿）。

    守门序固定（R-04）：① 成员 400 → ② bar 400 → ③ config 400 → ④ 区间 413 → ⑤ 并发 429（同步预占）。
    bar/config 的 400 **必须先于** 区间 413（``_guard_range`` 自身调 ``bar_to_seconds``，非法 bar 否则抛 500）。

    Returns:
        ``{jobId, runId, modelId}``。

    Raises:
        HTTPException: 400 成员 / bar / config 非法 ｜ 413 区间过长 ｜ 429 作业繁忙。
    """
    members = validate_members([m.model_dump() for m in req.members])   # ① 成员 400
    _validate_bar(req.bar)                                              # ② bar 400（先于 413）
    _validate_train_config(req.config)                                 # ③ config 400（先于 413）
    window = FactorWindow(inst_id=req.instId, bar=req.bar, start_ts=req.startTs,
                          end_ts=req.endTs, max_bars=req.maxBars)
    _guard_range(window)                                                # ④ 区间 413
    settings = get_settings()
    manager = get_weight_train_manager()
    if not manager.reserve():                                           # ⑤ 并发 429（同步预占硬上限，H-B3）
        raise HTTPException(429, detail={
            "code": "WEIGHT_TRAIN_BUSY",
            "message": f"并发训练作业已达上限 {settings.max_weight_train_jobs}，请稍后重试。"})
    # 守门全过（已预占名额）→ 解析 / 建模型（R-02）→ 落 running 记录 → 注册作业（此刻起即开缓冲，R-06）。
    # 预占后任何失败路径都 release，杜绝名额泄漏致长期假 429。
    try:
        model = await _resolve_model(session, req, members)
        run = await repo.add_weight_run(session, start=repo.WeightRunStart(
            model_id=model.id, method=req.method, inst_id=req.instId, bar=req.bar,
            start_ts=req.startTs, end_ts=req.endTs, horizon=req.horizon, config=req.config,
            started_ts=_now()))
        job = manager.start_job(JobSpec(                                # 内部 release()（预占转 running 计数）
            run_id=run.id, model_id=model.id, rest=rest, members=members, method=req.method,
            config=req.config, window=window, horizon=req.horizon,
            run_cap=settings.weight_model_run_cap, curve_steps=settings.weight_train_curve_steps))
    except Exception:
        manager.release()
        raise
    return {"jobId": job.job_id, "runId": run.id, "modelId": model.id}


def cancel_training(job_id: str) -> dict:
    """协作式取消训练作业（R-03）；返回管理器解析状态供路由映射 202/200/404。"""
    return {"jobId": job_id, "result": get_weight_train_manager().cancel(job_id)}


def _now() -> int:
    import time
    return int(time.time())
