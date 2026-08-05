"""路由：成果资产台账与持续样本外体检（discovery-asset-ledger §四）。

**不新增 WebSocket**（D-21）：体检是小时级低频，REST 轮询 + 手动刷新足够。
写类端点一律受 ``discovery_enabled`` 总闸（关闭 ⇒ 403 ``DISCOVERY_DISABLED``），与既有挖掘
端点一致；读类仍可看历史。

⚠️ 路由声明顺序承重：``/discovery/assets/export`` 与 ``/discovery/assets/summary`` 必须排在
``/discovery/assets/{assetId}`` **之前**，否则 FastAPI 会把 "export" 当成路径参数去解析 int。
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.session import get_session
from ..discovery import asset_export
from ..discovery import asset_repositories as asset_repo
from ..discovery import assets as asset_service
from ..discovery.health_runtime import get_health_guard

router = APIRouter(tags=["discovery-assets"])

_NOT_FOUND = {"code": "NOT_FOUND", "message": "成果资产不存在"}
NOTE_MAX = 256


class AssetNoteReq(BaseModel):
    """``PATCH /discovery/assets/{id}`` 的请求体（服务端再截断一次，前端不可信）。"""

    note: str = Field(default="", max_length=NOTE_MAX)


def _bad(code: str, message: str, status: int) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


def _require_enabled() -> None:
    if not bool(get_settings().discovery_enabled):
        raise _bad("DISCOVERY_DISABLED", "策略与因子挖掘已被管理员关闭", 403)


async def _require_asset(session: AsyncSession, asset_id: int):
    row = await asset_repo.get_asset(session, asset_id)
    if row is None:
        raise HTTPException(404, detail=_NOT_FOUND)
    return row


def _query_of(params: dict) -> asset_repo.AssetQuery:
    return asset_repo.AssetQuery(**params)


# ============================ 台账列表 / 摘要 / 导出 ============================
@router.get("/discovery/assets")
async def list_assets(kind: str = "", status: str = "", promoted: str = "",
                      archived: str = "0", instId: str = "", bar: str = "",
                      mechanism: str = "", q: str = "", sort: str = "lastFound",
                      order: str = "desc", limit: int = Query(50, ge=1, le=200),
                      offset: int = Query(0, ge=0),
                      session: AsyncSession = Depends(get_session)) -> dict:
    rows, total = await asset_repo.list_assets(session, _query_of({
        "kind": kind, "status": status, "promoted": promoted, "archived": archived,
        "inst_id": instId, "bar": bar, "mechanism": mechanism, "q": q, "sort": sort,
        "order": order, "limit": limit, "offset": offset}))
    return {"items": [asset_export.asset_item(row) for row in rows], "total": total}


@router.get("/discovery/assets/summary")
async def assets_summary(session: AsyncSession = Depends(get_session)) -> dict:
    """台账摘要 + 守护自身状态（``guard`` 段来自守护的内存态，不落库）。"""
    summary = await asset_repo.summary_counts(session, now=int(time.time()))
    return {**summary, "guard": get_health_guard().state()}


@router.get("/discovery/assets/export")
async def export_assets(ids: str = "", kind: str = "", status: str = "", promoted: str = "",
                        archived: str = "0", instId: str = "", bar: str = "",
                        mechanism: str = "", q: str = "", sort: str = "lastFound",
                        order: str = "desc",
                        session: AsyncSession = Depends(get_session)) -> Response:
    """两种模式**互斥且行为不同**（§4.8）。

    - 给了 ``ids``：超 500 ⇒ 413。用户点名要哪些，就不能悄悄少给几条。
    - 没给 ``ids``（走筛选）：取前 500 并回 ``truncated`` / ``matched``，**绝不** 413——
      筛选式导出本来就是「给我看看这一批」，413 会让用户无从下手。
    """
    wanted = [int(value) for value in ids.split(",") if value.strip().isdigit()]
    if ids and len(wanted) > asset_export.EXPORT_HARD_LIMIT:
        raise _bad("DISCOVERY_EXPORT_TOO_LARGE",
                   f"一次最多导出 {asset_export.EXPORT_HARD_LIMIT} 条，请缩小选择范围", 413)
    if wanted:
        rows = [await _require_asset(session, value) for value in wanted]
        payload = await asset_export.build_snapshot(session, rows)
    else:
        rows, total = await asset_repo.list_assets(session, _query_of({
            "kind": kind, "status": status, "promoted": promoted, "archived": archived,
            "inst_id": instId, "bar": bar, "mechanism": mechanism, "q": q, "sort": sort,
            "order": order, "limit": asset_export.EXPORT_HARD_LIMIT, "offset": 0}))
        payload = await asset_export.build_snapshot(session, rows, matched=total)
    return Response(
        content=json.dumps(payload, ensure_ascii=False), media_type="application/json",
        headers={"Content-Disposition":
                 f'attachment; filename="{asset_export.filename_of()}"'})


# ============================ 单条资产 ============================
@router.get("/discovery/assets/{asset_id}")
async def get_asset(asset_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    return await asset_export.asset_detail(session, await _require_asset(session, asset_id))


@router.get("/discovery/assets/{asset_id}/checks")
async def list_asset_checks(asset_id: int, limit: int = Query(50, ge=1, le=200),
                            offset: int = Query(0, ge=0),
                            session: AsyncSession = Depends(get_session)) -> dict:
    await _require_asset(session, asset_id)
    rows, total = await asset_repo.list_checks(session, asset_id, limit=limit, offset=offset)
    return {"items": [asset_export.check_item(row) for row in rows], "total": total}


@router.post("/discovery/assets/{asset_id}/check")
async def check_asset_now(asset_id: int,
                          session: AsyncSession = Depends(get_session)) -> dict:
    """立即体检（同步跑一次，最长约 ``discovery_health_asset_timeout_s`` + 取数时间）。

    与守护的互斥用**同步预占**（``claim`` 无 ``await``，杜绝 TOCTOU）；``finally`` 里一定释放，
    包括请求被客户端断开的情况。
    """
    _require_enabled()
    await _require_asset(session, asset_id)
    guard = get_health_guard()
    if not guard.claim(asset_id):
        raise _bad("DISCOVERY_HEALTH_BUSY", "该资产正在体检中，请稍后再试", 409)
    try:
        row = await guard.check_now(asset_id)
    finally:
        guard.release(asset_id)
    if row is None:
        raise _bad("DISCOVERY_DATA_UNAVAILABLE", "体检未产出记录", 503)
    return asset_export.check_item(row)


@router.post("/discovery/assets/{asset_id}/archive")
async def archive_asset(asset_id: int,
                        session: AsyncSession = Depends(get_session)) -> dict:
    return await _set_archived(session, asset_id, archived=True)


@router.post("/discovery/assets/{asset_id}/unarchive")
async def unarchive_asset(asset_id: int,
                          session: AsyncSession = Depends(get_session)) -> dict:
    return await _set_archived(session, asset_id, archived=False)


async def _set_archived(session: AsyncSession, asset_id: int, *, archived: bool) -> dict:
    """归档不删数据、不动实体、不影响已入库的 factor / strategy 行（AL-6）。"""
    _require_enabled()
    await _require_asset(session, asset_id)
    row = await asset_repo.set_archived(session, asset_id, archived=archived,
                                        now=int(time.time()))
    return {"id": int(row.id), "archived": bool(row.archived),
            "archivedTs": int(row.archived_ts or 0)}


@router.patch("/discovery/assets/{asset_id}")
async def patch_asset(asset_id: int, body: AssetNoteReq,
                      session: AsyncSession = Depends(get_session)) -> dict:
    _require_enabled()
    await _require_asset(session, asset_id)
    row = await asset_repo.set_note(session, asset_id, body.note)
    return asset_export.asset_item(row)


@router.post("/discovery/assets/{asset_id}/promote")
async def promote_asset(asset_id: int,
                        session: AsyncSession = Depends(get_session)) -> dict:
    """从台账入库（**不经候选行**，run 可能已删）；响应与候选入库端点同形状。"""
    _require_enabled()
    await _require_asset(session, asset_id)
    return await asset_service.promote_asset(session, asset_id)
