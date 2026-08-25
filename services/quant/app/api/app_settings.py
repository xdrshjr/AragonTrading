"""路由：设置中心 · 自动交易设置 + 交易经验库阈值。

``GET/PUT /api/settings/auto-trade``（auto-trade-budget-and-allocation-settings §4.1）与
``GET/PUT /api/settings/experience``（ai-trade-experience-context-graph §五）并列——一致性优先。

``GET/PUT /api/settings/auto-trade``：读写总预算上限百分比 ``maxBudgetPct``（DB 持久化、服务端权威）。
响应另回 ``effectiveMaxBudgetPct = min(maxBudgetPct, 分组运营天花板×100)`` 供前端预览口径（RN-2），
该值由 ``settings.live_auto_group_max_capital_frac`` 计算、不入库。错误体统一 ``{error:{code,message}}``
（经 main 全局处理器包装）；越界 0<pct≤100 由 schema 触发 422。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_auto.experience.disclosure import budget_floor
from ..ai_auto.experience.service import resolve_experience_settings, setting_bridge
from ..config import Settings, get_settings
from ..db import repositories as repo
from ..db.session import get_session
from ..schemas import (
    AutoTradeSettingsIn,
    AutoTradeSettingsOut,
    ExperienceSettingsIn,
    ExperienceSettingsOut,
)

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


# ---------------- 交易经验库阈值（ai-trade-experience-context-graph §五）----------------
async def _experience_wire(session: AsyncSession, settings: Settings) -> dict:
    """把**运行时真正会读到的那份** `Settings` 渲染成面板回执。

    刻意不直接读 `app_settings` 的 12 个键：那样回执说的是「库里存了什么」，而用户想知道的是
    「系统实际会用什么」。两者在「DB 里有个越界值被夹紧了」时并不相等——经
    `resolve_experience_settings` 取值，面板显示的就恒是生效值（不变式 I-10）。
    """
    derived = await resolve_experience_settings(session, settings)
    wire = {wire_key: getattr(derived, field)
            for wire_key, _app_key, field, _caster in setting_bridge()}
    # `enabled` 是**运维总闸**，本端点上只读（评审 P2-4）：它可写就会与启动弹窗那个复选框互相
    # 矛盾——总闸关掉后弹窗仍显示勾选且可勾。总闸只经环境变量 / `.env` 调整。
    wire["enabled"] = bool(derived.ai_trade_experience_enabled)
    # 下限如实下发，使 UI 能在提交前就说清楚为什么不合法（I-8：算了却看不见等于死码）。
    wire["l0MinChars"] = budget_floor("l0")
    wire["l1MinChars"] = budget_floor("l1")
    return wire


@router.get("/settings/experience", response_model=ExperienceSettingsOut)
async def read_experience_settings(session: AsyncSession = Depends(get_session),
                                   settings: Settings = Depends(get_settings)) -> dict:
    """读经验库阈值面板（缺记录回 `config.py` 默认；`enabled` 只读）。"""
    return await _experience_wire(session, settings)


@router.put("/settings/experience", response_model=ExperienceSettingsOut)
async def update_experience_settings(body: ExperienceSettingsIn,
                                     session: AsyncSession = Depends(get_session),
                                     settings: Settings = Depends(get_settings)) -> dict:
    """写经验库阈值（只写用户显式给出的字段；越界 422）。

    **★ E-7（评审 P1-1）· 字符预算的下限必须复用 `budget_floor`**：`_check_experience_budgets`
    只在 `Settings` 构造时跑，本端点会**完全绕过**它——不在这里再判一次，用户就能从界面上把 L0
    配成一个「永远渲染不出任何东西」的值而毫无提示（整层零条目 ⇒ 整段省略，日志全绿）。
    判据取自**同一个函数**，绝不在这里抄一份数字（用例 A-40）。

    写完立刻回读**生效值**——「保存成功」与「真的生效了」不是一回事，而这块面板此前连一条读路径
    都没有（不变式 I-10 / 用例 A-41）。
    """
    patch = body.model_dump(exclude_none=True)
    for layer in ("l0", "l1"):
        key = f"{layer}MaxChars"
        floor = budget_floor(layer)
        if key in patch and int(patch[key]) < floor:
            raise HTTPException(422, detail={
                "code": "EXPERIENCE_BUDGET_TOO_SMALL",
                "message": f"{key} 必须 ≥ {floor}（框架句 + 单条经验上限 120 字 + 序号换行 4 字），"
                           "否则该层第一条经验就装不下，整层一个字都不会渲染"})
    for wire_key, app_key, _field, caster in setting_bridge():
        if wire_key not in patch:
            continue
        value = bool(patch[wire_key]) if caster is bool else caster(patch[wire_key])
        await repo.set_app_setting(session, app_key, value)
    return await _experience_wire(session, settings)
