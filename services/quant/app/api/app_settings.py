"""路由：设置中心 · 自动交易设置（auto-trade-budget-and-allocation-settings §4.1）。

``GET/PUT /api/settings/auto-trade``：读写总预算上限百分比 ``maxBudgetPct``（DB 持久化、服务端权威）。
响应另回 ``effectiveMaxBudgetPct = min(maxBudgetPct, 分组运营天花板×100)`` 供前端预览口径（RN-2），
该值由 ``settings.live_auto_group_max_capital_frac`` 计算、不入库。错误体统一 ``{error:{code,message}}``
（经 main 全局处理器包装）；越界 0<pct≤100 由 schema 触发 422。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..db import repositories as repo
from ..db.session import get_session
from ..schemas import AutoTradeSettingsIn, AutoTradeSettingsOut

router = APIRouter(tags=["settings"])


def _effective_max_budget_pct(max_budget_pct: float, settings: Settings) -> float:
    """RN-2：有效上限 = min(用户配置, 分组运营天花板×100)，使预览不高估真实可用预算。"""
    return min(max_budget_pct, settings.live_auto_group_max_capital_frac * 100.0)


@router.get("/settings/auto-trade", response_model=AutoTradeSettingsOut)
async def read_auto_trade_settings(session: AsyncSession = Depends(get_session),
                                   settings: Settings = Depends(get_settings)) -> dict:
    """读自动交易设置（缺记录回默认 100%）+ 注入 effectiveMaxBudgetPct（只读）。"""
    values = await repo.get_auto_trade_settings(session)
    pct = values["maxBudgetPct"]
    return {"maxBudgetPct": pct, "effectiveMaxBudgetPct": _effective_max_budget_pct(pct, settings)}


@router.put("/settings/auto-trade", response_model=AutoTradeSettingsOut)
async def update_auto_trade_settings(body: AutoTradeSettingsIn,
                                     session: AsyncSession = Depends(get_session),
                                     settings: Settings = Depends(get_settings)) -> dict:
    """写总预算上限百分比（边界 0<pct≤100 由 schema 校验）后回写最终值 + effectiveMaxBudgetPct。"""
    values = await repo.set_auto_trade_settings(session, max_budget_pct=body.maxBudgetPct)
    pct = values["maxBudgetPct"]
    return {"maxBudgetPct": pct, "effectiveMaxBudgetPct": _effective_max_budget_pct(pct, settings)}
