"""路由：因子库 / 因子 CRUD / 计算·评估·挖掘 / 因子组合 + 训练·打分 (spec §4)。

字段 camelCase、时间秒级 UTC、错误体统一 ``{error:{code,message}}``（沿用 main.py 异常处理器）。
取数 / 计算编排已抽到 ``factors/service.py``（线程池调度，UI 与 Agent 共用，R6）；本路由仅做请求
解包 + 调用 service。``get_rest_client`` 在本命名空间解析，故测试对 ``app.api.factors.get_rest_client``
的 monkeypatch 仍生效（注入给 service）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import repositories as repo
from ..db.session import get_session
from ..engine.factors import available_factors, available_trainers
from ..engine.factors.evaluation import DEFAULT_HORIZONS
from ..factors import service
from ..factors.service import FactorWindow
from ..factors.specs import validate_factor_key
from ..okx.rest_client import get_rest_client
from ..schemas import (
    FactorComputeReq,
    FactorCreateReq,
    FactorEvaluateReq,
    FactorMineReq,
    FactorScoreInlineReq,
    FactorScoreReq,
    FactorSetCreateReq,
    FactorSetUpdateReq,
    FactorTrainReq,
    FactorUpdateReq,
    factor_out,
    factor_set_out,
)

router = APIRouter(tags=["factors"])


def _window(body) -> FactorWindow:
    return FactorWindow(inst_id=body.instId, bar=body.bar, start_ts=body.startTs,
                        end_ts=body.endTs, max_bars=getattr(body, "maxBars", None))


def _horizons(raw: list[int] | None) -> tuple[int, ...]:
    return tuple(raw) if raw else DEFAULT_HORIZONS


# ============================ 因子库与因子 CRUD（§4.1） ============================
@router.get("/factors/library")
async def factor_library() -> list[dict]:
    """静态因子库目录（key/cn/category/directionHint/paramsSchema）。"""
    return available_factors()


@router.get("/factors/trainers")
async def factor_trainers() -> list[dict]:
    """训练器目录 + 可用性（rf/xgb 缺库即 available=false，前端据此禁用选项）。"""
    return available_trainers()


@router.get("/factors")
async def list_factors(enabled: bool | None = Query(None), category: str | None = Query(None),
                       q: str | None = Query(None), source: str | None = Query(None),
                       sort: str | None = Query(None),
                       session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = await repo.list_factors(session, enabled=enabled, category=category,
                                   q=q, source=source, sort=sort)
    return [factor_out(r) for r in rows]


@router.post("/factors", status_code=201)
async def create_factor(body: FactorCreateReq,
                        session: AsyncSession = Depends(get_session)) -> dict:
    validate_factor_key(body.factorKey)
    from ..engine.factors import factor_meta
    meta = factor_meta(body.factorKey) or {}
    values = repo.FactorValues(
        factor_key=body.factorKey, name=body.name, params=body.params, direction=body.direction,
        category=str(meta.get("category", "")), source=body.source, notes=body.notes)
    return factor_out(await repo.create_factor(session, values=values))


@router.put("/factors/{factor_id}")
async def update_factor(factor_id: int, body: FactorUpdateReq,
                        session: AsyncSession = Depends(get_session)) -> dict:
    row = await repo.update_factor(session, factor_id, patch=repo.FactorPatch(
        name=body.name, params=body.params, direction=body.direction,
        enabled=body.enabled, notes=body.notes))
    if row is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "因子不存在"})
    return factor_out(row)


@router.delete("/factors/{factor_id}", status_code=204)
async def delete_factor(factor_id: int, session: AsyncSession = Depends(get_session)) -> Response:
    result = await repo.delete_factor(session, factor_id)
    if result == "not_found":
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "因子不存在"})
    if result == "builtin":
        raise HTTPException(409, detail={"code": "BUILTIN_READONLY", "message": "内置因子不可删除，请改用禁用"})
    return Response(status_code=204)


# ============================ 计算 / 评估 / 挖掘（§4.2） ============================
@router.post("/factors/compute")
async def compute_factor(body: FactorComputeReq,
                         session: AsyncSession = Depends(get_session)) -> dict:
    _ = session
    return await service.compute(get_rest_client(), _window(body),
                                 factor_key=body.factorKey, params=body.params)


@router.post("/factors/evaluate")
async def evaluate_factor(body: FactorEvaluateReq,
                          session: AsyncSession = Depends(get_session)) -> dict:
    factor_key, params, direction = body.factorKey, body.params, body.direction
    if body.factorId is not None:
        row = await repo.get_factor(session, body.factorId)
        if row is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "因子不存在"})
        factor_key = factor_key or row.factor_key
        if not params:
            params = json.loads(row.params_json or "{}")
        if direction == "auto" and row.direction in ("long", "short"):
            direction = row.direction
    if not factor_key:
        raise HTTPException(400, detail={"code": "FACTOR_REQUIRED", "message": "需提供 factorKey 或 factorId"})
    return await service.evaluate(
        session, get_rest_client(), _window(body), factor_key=factor_key, params=params,
        direction=direction, horizons=_horizons(body.horizons), quantiles=body.quantiles,
        factor_id=body.factorId)


@router.post("/factors/mine")
async def mine_factors(body: FactorMineReq,
                       session: AsyncSession = Depends(get_session)) -> dict:
    return await service.mine(
        session, get_rest_client(), _window(body), categories=body.categories,
        horizons=_horizons(body.horizons), top_n=body.topN, persist=body.persist)


# ============================ 因子组合（factor sets）与训练（§4.3） ============================
@router.get("/factor-sets")
async def list_factor_sets(enabled: bool | None = Query(None),
                           session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = await repo.list_factor_sets(session, enabled=enabled)
    return [factor_set_out(r) for r in rows]


@router.get("/factor-sets/{set_id}")
async def get_factor_set(set_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    row = await repo.get_factor_set(session, set_id)
    if row is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "因子组合不存在"})
    return factor_set_out(row)


@router.post("/factor-sets", status_code=201)
async def create_factor_set(body: FactorSetCreateReq,
                            session: AsyncSession = Depends(get_session)) -> dict:
    # 编排单源化到 service.create_set（与 Agent save_factor_set 共用，字节级等价，R2）：
    # validate_members → normalize_thresholds → assert_norm_contract → assert_weights_cover → 落库 → factor_set_out。
    return await service.create_set(
        session, name=body.name, members_raw=[m.model_dump() for m in body.members],
        method=body.method, config=body.config, model=body.modelJson,
        metrics=body.metricsJson, enabled=body.enabled)


@router.put("/factor-sets/{set_id}")
async def update_factor_set(set_id: int, body: FactorSetUpdateReq,
                            session: AsyncSession = Depends(get_session)) -> dict:
    from ..factors.specs import (
        assert_norm_contract,
        assert_weights_cover,
        normalize_thresholds,
        validate_members,
    )
    members = None
    if body.members is not None:
        members = validate_members([m.model_dump() for m in body.members])
    # partial update：仅在请求同时带上对应字段时校验，避免误伤局部更新（H2/H3，评审 §8）。
    if body.modelJson is not None:
        if body.config is not None:
            assert_norm_contract(normalize_thresholds((body.config or {}).get("thresholds")), body.modelJson)
        if members is not None:
            assert_weights_cover(members, body.modelJson)
    row = await repo.update_factor_set(session, set_id, patch=repo.FactorSetPatch(
        name=body.name, members=members, method=body.method, config=body.config,
        model=body.modelJson, metrics=body.metricsJson, enabled=body.enabled))
    if row is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "因子组合不存在"})
    return factor_set_out(row)


@router.delete("/factor-sets/{set_id}", status_code=204)
async def delete_factor_set(set_id: int, session: AsyncSession = Depends(get_session)) -> Response:
    result = await repo.delete_factor_set(session, set_id)
    if result == "not_found":
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "因子组合不存在"})
    return Response(status_code=204)


@router.post("/factor-sets/train")
async def train_factor_set(body: FactorTrainReq,
                           session: AsyncSession = Depends(get_session)) -> dict:
    _ = session
    return await service.train(
        get_rest_client(), _window(body), members=[m.model_dump() for m in body.members],
        method=body.method, config=body.config, horizon=body.horizon)


@router.post("/factor-sets/score-preview")
async def score_inline(body: FactorScoreInlineReq,
                       session: AsyncSession = Depends(get_session)) -> dict:
    _ = session
    return await service.score_inline(
        get_rest_client(), _window(body), members=[m.model_dump() for m in body.members],
        thresholds=body.thresholds)


@router.post("/factor-sets/{set_id}/score")
async def score_factor_set(set_id: int, body: FactorScoreReq,
                           session: AsyncSession = Depends(get_session)) -> dict:
    row = await repo.get_factor_set(session, set_id)
    if row is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "因子组合不存在"})
    return await service.score_set(get_rest_client(), _window(body), row)
