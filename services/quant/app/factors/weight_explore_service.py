"""因子组合探索训练 service 编排（factor-combo-exploration §2.3）。

探索训练在存储 / 运行时层面**就是一个批次**（``kind="explore"``），唯一一般化是候选携各自组合成员。镜像
``weight_batch_service.start_batch`` 的「守门优先、全过后才落库 / 注册运行时」范式（绝不留孤儿）：全部 400
（池 / bar / config / 方法 / 计划 / 候选数）一律**先于** 413 区间守门（P1-2）；候选数超上限在**展开后、取数 /
注册前** 显式 400 阻断，绝不静默截断（P1-3）。读 / 择优 / 取消 / 删除 / WS 全部复用 ``weight_batches`` 既有端点
（探索批次就是批次）。取数 / 计算全程真实 OKX，绝不假数据、绝不静默降级。
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import repositories as repo
from ..db.models import WeightTrainBatch
from ..schemas import WeightExploreStartReq, weight_explore_start_out
from .service import FactorWindow
from .specs import validate_members
from .weight_batch_manager import get_weight_batch_manager
from .weight_batch_runtime import BatchSharedSpec, CandidateSpec, WeightBatchRuntime
from .weight_explore_planner import ComboPlan, plan_combinations
from .weight_model_service import _guard_range, _validate_bar, _validate_train_config
from .weight_train_manager import get_weight_train_manager


def _bad(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


@dataclass(frozen=True)
class _ExploreContext:
    """探索启动的守门后不可变上下文（聚合传参，控行数 / 参数数，C-6）。"""

    req: WeightExploreStartReq
    pool: list[dict]
    methods: list[str]
    plan: ComboPlan
    shared_config: dict
    plan_json: dict


def _explore_label(pool: list[dict]) -> str:
    """缺省批次名 = 因子池摘要 + 「组合探索」（前若干因子 key 拼接，便于列表识别）。"""
    keys = [str(m.get("factorKey", "")) for m in pool if m.get("factorKey")]
    head = "·".join(keys[:3])
    suffix = f" 等{len(keys)}因子" if len(keys) > 3 else ""
    return f"{head}{suffix} 组合探索" if head else "因子组合探索"


def _plan_json(plan: ComboPlan, req: WeightExploreStartReq, pool: list[dict],
               methods: list[str]) -> dict:
    """探索计划快照（落 ``explore_plan_json`` + 响应回显）：策略 / 参数 / 方法 / 池 key / 组合展示元数据。"""
    return {
        "strategy": req.strategy, "params": dict(req.params or {}), "methods": methods,
        "poolKeys": [str(m.get("factorKey", "")) for m in pool],
        "combos": [{"id": f"C{i}", "label": plan.labels[i],
                    "memberKeys": [str(m.get("factorKey", "")) for m in combo]}
                   for i, combo in enumerate(plan.combos)],
    }


def _validate_inputs(req: WeightExploreStartReq) -> tuple[list[dict], list[str], dict]:
    """廉价 400 守门（① 池 → ② bar → ③ config → ④ 方法去重）；全过返回 (pool, methods, shared_config)。"""
    pool = validate_members([m.model_dump() for m in req.pool])       # ① 池成员 400（复用 specs）
    _validate_bar(req.bar)                                            # ② bar 400（复用 weight_model_service）
    shared_config = dict(req.config or {})
    _validate_train_config(shared_config)                            # ③ 共享 config 400（复用）
    methods = list(dict.fromkeys(req.methods))                       # ④ 方法去重（Pydantic 已按 Literal 校验合法性）
    if not methods:
        raise _bad("WEIGHT_EXPLORE_NO_METHOD", "至少选择一个训练方法。")
    return pool, methods, shared_config


def _guard_candidate_count(count: int) -> None:
    """候选数（组合 × 方法）上下限守门（400，均在取数 / 注册前）：超上限提示收窄、绝不静默截断（P1-3）；不足 2 无法对比。"""
    cap = get_settings().max_weight_explore_candidates
    if count > cap:
        raise _bad("WEIGHT_EXPLORE_TOO_MANY_CANDIDATES",
                   f"探索候选数 {count}（组合 × 方法）超过上限 {cap}，请收窄组合策略或减少训练方法后重试。")
    if count < 2:
        raise _bad("WEIGHT_EXPLORE_TOO_FEW_CANDIDATES",
                   "探索至少需要 2 个候选才能对比，请扩大组合策略或增加训练方法。")


async def start_exploration(session: AsyncSession, rest, req: WeightExploreStartReq) -> dict:
    """启动因子组合探索训练：**守门优先、全过后才落库 / 注册运行时**（绝不留孤儿）。

    守门序（P1-2，全部 400 先于 413）：① 池 → ② bar → ③ config → ④ 方法 → ⑤ 生成组合 → ⑥ 展开候选 + 候选数上限
    → ⑦ 区间（末步 413）。全过 → 建 explore 批次（父模型 source="batch"）+ 逐候选 queued run（携各自组合成员）
    → 注册既有 ``WeightBatchRuntime``（复用运行时；仅一般化为「按候选成员」透传）。

    Returns:
        ``{batchId, parentModelId, label, kind:"explore", origin, explorePlan, candidates:[...]}``。

    Raises:
        HTTPException: 400 池 / bar / config / 无方法 / 非法计划 / 候选数越界 ｜ 413 区间过长。
    """
    pool, methods, shared_config = _validate_inputs(req)             # ①–④ 400
    try:
        plan = plan_combinations(pool, strategy=req.strategy, params=dict(req.params or {}))  # ⑤ 生成组合
    except ValueError as exc:
        raise _bad("WEIGHT_EXPLORE_INVALID_PLAN", str(exc))
    _guard_candidate_count(len(plan.combos) * len(methods))          # ⑥ 展开候选 + 候选数上限 400（取数前）
    _guard_range(FactorWindow(inst_id=req.instId, bar=req.bar, start_ts=req.startTs,
                              end_ts=req.endTs, max_bars=req.maxBars))  # ⑦ 区间 413（末步，候选集已定）
    ctx = _ExploreContext(req=req, pool=pool, methods=methods, plan=plan,
                          shared_config=shared_config,
                          plan_json=_plan_json(plan, req, pool, methods))
    return await _persist_and_register(session, rest, ctx)


async def _persist_and_register(session: AsyncSession, rest, ctx: _ExploreContext) -> dict:
    """守门全过 → 建父模型（source="batch"）+ explore 批次行 + 逐候选 queued run → 注册运行时 → 返回响应。"""
    settings = get_settings()
    req, label = ctx.req, _explore_label(ctx.pool)
    parent = await repo.create_weight_model(session, spec=repo.WeightModelSpec(
        name=label, inst_id=req.instId, bar=req.bar, members=ctx.pool, method=ctx.methods[0],
        config=ctx.shared_config, horizon=req.horizon, source="batch", factor_set_id=req.factorSetId))
    batch = await repo.create_weight_batch(session, spec=repo.WeightBatchSpec(
        label=label, inst_id=req.instId, bar=req.bar, start_ts=req.startTs, end_ts=req.endTs,
        horizon=req.horizon, members=ctx.pool, config=ctx.shared_config, parent_model_id=parent.id,
        factor_set_id=req.factorSetId, origin=req.origin, kind="explore", explore_plan=ctx.plan_json))
    candidates, roster = await _create_candidate_runs(session, batch, parent.id, ctx)
    runtime = WeightBatchRuntime(
        batch_id=batch.id, parent_model_id=parent.id, rest=rest,
        manager=get_weight_train_manager(),
        spec=BatchSharedSpec(
            inst_id=req.instId, bar=req.bar, start_ts=req.startTs, end_ts=req.endTs,
            max_bars=req.maxBars, horizon=req.horizon, members=ctx.pool, config=ctx.shared_config,
            run_cap=settings.weight_model_run_cap, curve_steps=settings.weight_train_curve_steps),
        candidates=candidates)
    get_weight_batch_manager().register(runtime)
    return weight_explore_start_out(batch, ctx.plan_json, roster)


async def _create_candidate_runs(session: AsyncSession, batch: WeightTrainBatch, parent_id: int,
                                 ctx: _ExploreContext) -> tuple[list[CandidateSpec], list[dict]]:
    """逐候选（组合 × 方法，组合外层 / 方法内层）落 queued run 并组装运行时 CandidateSpec + 响应 roster。

    每个组合的全部方法候选共享稳定组合 id（``variant_label="C{i}"``，展示名走 ``explore_plan_json.combos[].label``，
    绝不把长成员串塞进 ``variant_label(String(64))``）；候选自有组合成员经 ``WeightRunStart.members`` 落 run.members_json。
    """
    candidates: list[CandidateSpec] = []
    roster: list[dict] = []
    req = ctx.req
    for combo_index, combo in enumerate(ctx.plan.combos):
        cid = f"C{combo_index}"
        for method in ctx.methods:
            run = await repo.add_queued_weight_run(session, start=repo.WeightRunStart(
                model_id=parent_id, method=method, inst_id=req.instId, bar=req.bar,
                start_ts=req.startTs, end_ts=req.endTs, horizon=req.horizon,
                config=ctx.shared_config, started_ts=0, batch_id=batch.id,
                variant_label=cid, members=combo))
            candidates.append(CandidateSpec(run_id=run.id, method=method, config={},
                                            variant_label=cid, members=combo))
            roster.append({"runId": run.id, "method": method, "variantLabel": cid,
                           "comboLabel": ctx.plan.labels[combo_index], "status": "queued"})
    return candidates, roster
