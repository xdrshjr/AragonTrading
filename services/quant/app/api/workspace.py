"""路由：workspace 偏好持久化（watchlist-auto-trade-persistence §3）。

``GET/PUT /api/workspace/watchlist``：把用户**自选标的** + **自动交易面板成员** + **分配模式**持久化到
后端 SQLite（``AppSetting`` 通用 KV，key ``workspace.watchlist_prefs``），使桌面端强杀 / 崩溃重启后
偏好仍在（替代仅落 renderer ``localStorage`` 的易失方案，§1）。

命名（P2-5）：端点名取 ``watchlist``，但载荷是整个「watchlist 工作区偏好」（自选 + 成员 + 分配模式）；
路由文件 ``workspace.py`` 与 key ``workspace.watchlist_prefs`` 表达该更宽语义。

边界职责（P1-A）：区间夹取 / 去重 / 截断 / ``members ⊆ watchlist`` 不变量全部由 repo sanitizer 负责，
schema 仅做类型校验，422 仅留给类型错误。错误体统一 ``{error:{code,message}}``（main 全局处理器包装）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import repositories as repo
from ..db.session import get_session
from ..schemas import WorkspaceWatchlistIn, WorkspaceWatchlistOut

router = APIRouter(tags=["workspace"])

# 无记录时回给前端的空默认（present:false → 前端 seed 当前本地切片，§2.2）。
_EMPTY_PREFS = {"watchlist": [], "autoTradeMembers": [], "autoTradeAllocationMode": "even",
                "symbolGroups": []}


@router.get("/workspace/watchlist", response_model=WorkspaceWatchlistOut)
async def read_workspace_watchlist(session: AsyncSession = Depends(get_session)) -> dict:
    """读 workspace 偏好；无记录回 ``present:false`` + 空默认（前端据此 seed 或 overwrite，§2.2）。"""
    prefs = await repo.get_workspace_watchlist(session)
    if prefs is None:
        return {"present": False, **_EMPTY_PREFS}
    return {"present": True, **prefs}


@router.put("/workspace/watchlist", response_model=WorkspaceWatchlistOut)
async def update_workspace_watchlist(body: WorkspaceWatchlistIn,
                                     session: AsyncSession = Depends(get_session)) -> dict:
    """整文档 upsert workspace 偏好（区间夹取 / 不变量由 repo sanitizer 负责）。

    回写夹取后的文档仅供前端**诊断 / 日志**——写穿 PUT 为 fire-and-forget，响应不回写 store（P1-B）。
    """
    prefs = await repo.set_workspace_watchlist(
        session,
        watchlist=body.watchlist,
        members=[{"instId": m.instId, "allocationPct": m.allocationPct} for m in body.autoTradeMembers],
        allocation_mode=body.autoTradeAllocationMode,
        symbol_groups=[
            {"id": g.id, "name": g.name, "symbols": g.symbols,
             "createdTs": g.createdTs, "updatedTs": g.updatedTs}
            for g in body.symbolGroups
        ],
    )
    return {"present": True, **prefs}
