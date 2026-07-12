"""路由：策略 CRUD + 触发回测 (spec §9.1)。

回测 / 对比的内部逻辑已抽到 `agent/services.py`（UI 与 Agent 共用，行为不变，R8）；
本路由仅做请求解包 + 调用复用 helper。`get_rest_client` 在本命名空间解析，故既有
测试对 `app.api.strategies.get_rest_client` 的 monkeypatch 仍生效（注入给 service）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent import services
from ..db import repositories as repo
from ..db.session import get_session
from ..factors.specs import is_factor, validate_factor_strategy_params
from ..okx.rest_client import get_rest_client
from ..schemas import (
    BacktestReq,
    ComboPreviewReq,
    CompareReq,
    CreateStrategyReq,
    MultiSampleBacktestReq,
    UpdateStrategyReq,
    WalkForwardReq,
)
from ..strategies.base import get_strategy_class

router = APIRouter(tags=["strategies"])


@router.get("/strategies")
async def list_strategies(enabled: bool | None = Query(None),
                          session: AsyncSession = Depends(get_session)) -> list[dict]:
    return await repo.list_strategies(session, enabled)


@router.get("/strategies/{strategy_id}")
async def get_strategy(strategy_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    s = await repo.get_strategy(session, strategy_id)
    if s is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "策略不存在"})
    return s


@router.post("/strategies", status_code=201)
async def create_strategy(body: CreateStrategyReq,
                          session: AsyncSession = Depends(get_session)) -> dict:
    if get_strategy_class(body.templateKey) is None:
        raise HTTPException(400, detail={"code": "BAD_TEMPLATE", "message": "模板不存在"})
    services.validate_combo_params(body.templateKey, body.params)
    # factor_score 的内联快照在创建边界一次性校验成员（否则非法快照会被回测期静默兜底，R1/评审 P1-1）。
    if is_factor(body.templateKey):
        validate_factor_strategy_params(body.params)
    return await repo.create_strategy(session, body.templateKey, body.name, body.params)


@router.put("/strategies/{strategy_id}")
async def update_strategy(strategy_id: int, body: UpdateStrategyReq,
                          session: AsyncSession = Depends(get_session)) -> dict:
    # 改成员时按该行模板做组合成员数边界校验（P2 纵深防御，防绕过 UI 存出非法组合）。
    if body.params is not None:
        existing = await repo.get_strategy(session, strategy_id)
        if existing is not None:
            services.validate_combo_params(existing["templateKey"], body.params)
            # factor_score 改成员同样在边界校验（is_factor 对孤儿模板返回 False，无 AttributeError，R1/P2-3）。
            if is_factor(existing["templateKey"]):
                validate_factor_strategy_params(body.params)
    s = await repo.update_strategy(session, strategy_id, name=body.name,
                                   params=body.params, enabled=body.enabled)
    if s is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "策略不存在"})
    return s


@router.delete("/strategies/{strategy_id}", status_code=204)
async def delete_strategy(strategy_id: int, session: AsyncSession = Depends(get_session)) -> Response:
    result = await repo.delete_strategy(session, strategy_id)
    if result == "not_found":
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "策略不存在"})
    if result == "builtin":
        raise HTTPException(409, detail={"code": "BUILTIN_READONLY",
                                         "message": "内置模板不可删除，请改用'另存为'或禁用"})
    return Response(status_code=204)


@router.post("/strategies/combo/preview")
async def combo_preview(body: ComboPreviewReq,
                        session: AsyncSession = Depends(get_session)) -> dict:
    """即时回测一个（未保存）组合，落一条 backtest run 以便用 runId 拉净值/成交。"""
    return await services.run_combo_preview(
        session, get_rest_client(),
        members_spec=[m.model_dump() for m in body.members],
        policy_spec=body.policy.model_dump() if body.policy else None,
        inst_id=body.instId, bar=body.bar, limit=body.limit,
        start_ts=body.startTs, end_ts=body.endTs, initial_capital=body.initialCapital,
        fees=body.fees, slippage=body.slippage, leverage=body.leverage,
    )


@router.post("/strategies/{strategy_id}/backtest")
async def backtest(strategy_id: int, body: BacktestReq,
                   session: AsyncSession = Depends(get_session)) -> dict:
    # 止盈止损一次性覆盖（backtest-stop-loss-take-profit §4.1）：仅并入非 None 键，键名对齐 RiskParams；
    # 两者皆空则 risk_override=None（下游 _prepare_strategy 空值短路 → 与现状字节级等价）。
    risk_override = {
        k: v for k, v in (("stop_loss_pct", body.stopLossPct),
                          ("take_profit_pct", body.takeProfitPct)) if v is not None
    } or None
    return await services.run_single_backtest(
        session, get_rest_client(),
        strategy_id=strategy_id, inst_id=body.instId, bar=body.bar, limit=body.limit,
        start_ts=body.startTs, end_ts=body.endTs, initial_capital=body.initialCapital,
        fees=body.fees, slippage=body.slippage, leverage=body.leverage,
        risk_override=risk_override,
    )


@router.post("/strategies/{strategy_id}/multi-backtest")
async def multi_backtest(strategy_id: int, body: MultiSampleBacktestReq,
                         session: AsyncSession = Depends(get_session)) -> dict:
    """多样本回测：同一策略对多个标的逐一回测（顺序执行 + per-symbol 错误隔离）。"""
    return await services.run_multi_sample_backtest(
        session, get_rest_client(),
        strategy_id=strategy_id, inst_ids=body.instIds, bar=body.bar, limit=body.limit,
        start_ts=body.startTs, end_ts=body.endTs, initial_capital=body.initialCapital,
        fees=body.fees, slippage=body.slippage, leverage=body.leverage,
    )


@router.post("/strategies/{strategy_id}/walk-forward")
async def walk_forward(strategy_id: int, body: WalkForwardReq,
                       session: AsyncSession = Depends(get_session)) -> dict:
    """高级回测：原子策略和保存后的组合策略共用 Walk-forward 路径。"""
    return await services.run_walk_forward(
        session, get_rest_client(),
        strategy_id=strategy_id, inst_id=body.instId, bar=body.bar,
        start_ts=body.startTs, end_ts=body.endTs, window_bars=body.windowBars,
        step_bars=body.stepBars, reset_capital=body.resetCapital,
        initial_capital=body.initialCapital, fees=body.fees, slippage=body.slippage,
        leverage=body.leverage, max_bars=body.maxBars,
    )


@router.post("/strategies/compare")
async def compare_strategies(body: CompareReq,
                             session: AsyncSession = Depends(get_session)) -> dict:
    return await services.run_compare(
        session, get_rest_client(),
        strategy_ids=body.strategyIds, inst_id=body.instId, bar=body.bar, limit=body.limit,
        start_ts=body.startTs, end_ts=body.endTs, initial_capital=body.initialCapital,
        fees=body.fees, slippage=body.slippage, leverage=body.leverage,
    )
