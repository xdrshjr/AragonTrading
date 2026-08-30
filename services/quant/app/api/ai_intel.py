"""路由：标的市场情报库（ai-trade-market-intel §4.1）。

九个 `/intel/*` 端点（另有三个 `/settings/intel*` 落在 `api/app_settings.py`，与既有两组设置端点
并列——一致性优先）。全部走 `/api` 前缀并经既有 `OriginGuardMiddleware`；错误体统一
`{error:{code,message}}`（`main.py` 全局处理器包装）。

三条贯穿本模块的纪律：

- **读端点一律不挂 `response_model`**（E-14）：它们回的是带嵌套结构的领域对象，未在模型里声明的
  wire 键会被 FastAPI **静默丢弃**——本仓库为此栽过（backstop-repair 评审）。
- **写类端点双闸 + instId 归属校验**：`_require_ai_enabled` 之外，`instId` 必须属于当前分组成员
  或用户自选（评审 P2-11）。没有这道校验，任意字符串都能建出 watch 行与条目，库里会长出永远不会
  被采集、也永远不会被清理的孤儿标的。读端点**不设**此闸——历史情报的标的可能已不在自选里，
  而那正是 G-4 要保住的场景。
- **每一次写都要 `invalidate`**：用户改完之后决策 prompt 里若还是旧的那一份，**这件事不会报任何
  错**（本仓库「写点有、读点无」的成文缺陷形状）。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_auto import get_ai_trade_manager
from ..ai_auto.intel import gate as intel_gate
from ..ai_auto.intel import runtime as intel_runtime
from ..ai_auto.intel import scheduler as intel_scheduler
from ..ai_auto.intel import service as intel_service
from ..ai_auto.intel import taxonomy as tax
from ..config import Settings, get_settings
from ..db import repositories as repo
from ..db.session import get_session
from ..schemas import IntelItemIn, IntelItemPatchIn, IntelMoveIn, IntelSeenIn
from .ai_auto import _require_ai_enabled

router = APIRouter(tags=["ai-intel"])


async def _require_known_inst(session: AsyncSession, inst_id: str) -> None:
    """写类端点的 `instId` 归属校验（评审 P2-11）。

    三条来源任一命中即放行：当前 AI 自动交易分组成员 / 用户自选 / 库里**已经有**这个标的的情报
    （最后一条让「历史标的的手工补录」仍然可行，而它不会凭空造出新的孤儿）。
    """
    status = get_ai_trade_manager().group_status() or {}
    if any(m.get("instId") == inst_id for m in status.get("members") or []):
        return
    prefs = await repo.get_workspace_watchlist(session) or {}
    if inst_id in (prefs.get("watchlist") or []):
        return
    if await repo.load_intel_watch(session, inst_id) is not None:
        return
    raise HTTPException(422, detail={
        "code": "intel_unknown_inst",
        "message": f"{inst_id} 既不在当前自动交易分组，也不在自选列表里"})


# 只有运行时知道的三个瞬态：轮是不是正在飞、本轮降级成什么、这个分组到底有没有装配上检索源。
# 其余全部是库存事实，必须取**当次查库**的值。
_RUNTIME_ONLY_KEYS = ("inFlight", "degrade", "providerConfigured")


async def _summary(session: AsyncSession, inst_id: str, settings: Settings) -> dict:
    """读端点的 summary：**库存事实查库、轮级瞬态取运行时缓存**。

    整份返回缓存是不对的：用户新增 / 删除一条之后，`invalidate` 只是**排了一个**异步刷新任务，
    而弹窗紧接着的这次 `GET` 会拿到尚未刷新的那一份——于是三层页签的计数比它下面的列表晚一个
    轮询周期。列表新鲜、计数陈旧，用户看到的是「我刚加的那条在列表里，可页签还说 2 条」。

    反过来整份查库也不对：`inFlight` 与本轮 `degrade` 在库里根本没有落点。
    """
    cached = intel_runtime.intel_summary_for(inst_id)
    fresh = await intel_service.read_summary(
        session, inst_id, settings=settings,
        provider_configured=intel_runtime.runtime_for(inst_id) is not None)
    if cached is None:
        return fresh
    return {**fresh, **{key: cached[key] for key in _RUNTIME_ONLY_KEYS if key in cached}}


@router.get("/intel/{instId}")
async def read_intel(instId: str, tier: str | None = None, category: str | None = None,
                     status: str = "active", q: str | None = None,
                     limit: int = Query(default=50, ge=1, le=200),
                     offset: int = Query(default=0, ge=0),
                     session: AsyncSession = Depends(get_session),
                     settings: Settings = Depends(get_settings)) -> dict:
    """情报库读取：summary + 三层条目 + 已归档总数。

    **不设总闸**：看历史情报不动任何资金，而 G-4 要保住的正是「停掉自动交易之后仍能查看」。
    """
    tiers: dict[str, dict] = {}
    for name in tax.TIERS:
        if tier and tier != name:
            tiers[name] = {"items": [], "total": 0}
            continue
        items, total = await repo.list_intel_items(
            session, instId, tier=name, category=category, status=status, q=q,
            limit=limit, offset=offset)
        tiers[name] = {"items": items, "total": total}
    counts = await repo.count_intel_by_tier(session, instId)
    return {"instId": instId, "summary": await _summary(session, instId, settings),
            "tiers": tiers, "archivedTotal": counts["archived"],
            "mutedTotal": counts["muted"]}


@router.get("/intel/{instId}/rounds")
async def read_intel_rounds(instId: str, kind: str | None = None,
                            limit: int = Query(default=20, ge=1, le=100),
                            session: AsyncSession = Depends(get_session)) -> dict:
    """采集 / 整理轮的审计时间线（含拒收码直方图）。

    这张表是「失败要出声」的存档面：一轮什么都没采到的原因在这里是**可回溯**的，而不是只在
    当时的 banner 上闪过。
    """
    return {"rounds": await repo.list_intel_rounds(session, instId, kind=kind, limit=limit)}


def _require_runtime(inst_id: str):
    """取该标的的情报运行时；未装配 / 未配置检索源 ⇒ 409（**诚实降级，绝不假装在采**）。"""
    runtime = intel_runtime.runtime_for(inst_id)
    if runtime is None or not runtime.enabled:
        raise HTTPException(409, detail={
            "code": "intel_no_provider",
            "message": tax.degrade_message("no_provider")})
    return runtime


@router.post("/intel/{instId}/collect", status_code=202)
async def trigger_collect(instId: str, session: AsyncSession = Depends(get_session),
                          settings: Settings = Depends(get_settings)) -> dict:
    """手动触发一轮采集（**排队即返回**，故前端超时档走 FAST 即可）。"""
    _require_ai_enabled(settings)
    await _require_known_inst(session, instId)
    runtime = _require_runtime(instId)
    if runtime.lock.locked():
        raise HTTPException(409, detail={"code": "intel_busy",
                                         "message": "该标的已有一轮情报正在进行中"})
    watch = await repo.load_intel_watch(session, instId) or {}
    if intel_service.daily_used(watch) >= int(settings.ai_trade_intel_daily_round_cap):
        raise HTTPException(429, detail={
            "code": "intel_budget_exhausted",
            "message": tax.degrade_message("budget_exhausted")})
    intel_scheduler.schedule(runtime.run_collect(instId, force=True))
    return {"queued": True}


@router.post("/intel/{instId}/curate", status_code=202)
async def trigger_curate(instId: str, session: AsyncSession = Depends(get_session),
                         settings: Settings = Depends(get_settings)) -> dict:
    """手动触发一轮整理。"""
    _require_ai_enabled(settings)
    await _require_known_inst(session, instId)
    runtime = _require_runtime(instId)
    if runtime.lock.locked():
        raise HTTPException(409, detail={"code": "intel_busy",
                                         "message": "该标的已有一轮情报正在进行中"})
    intel_scheduler.schedule(runtime.run_curate(instId))
    return {"queued": True}


@router.post("/intel/{instId}/items", status_code=201)
async def create_intel_item(instId: str, body: IntelItemIn,
                            session: AsyncSession = Depends(get_session),
                            settings: Settings = Depends(get_settings)) -> dict:
    """手工新增一条情报（`origin="user"`，落 `user_edited=True` 免死金牌）。

    **不做乐观更新**：服务端会算 `dedup_key` 并可能判重，前端必须等回执。
    """
    _require_ai_enabled(settings)
    await _require_known_inst(session, instId)
    derived = await intel_service.resolve_intel_settings(session, settings)
    limits = tax.limits_from_settings(derived)
    now = int(time.time())
    sources = [{"url": s.url, "title": s.title, "publisher": tax.normalize_domain(s.url),
                "domain": tax.normalize_domain(s.url), "publishedTs": int(s.publishedTs or 0)}
               for s in body.sources]
    event_ts = int(body.eventTs or 0)
    payload = {
        "instId": instId, "tier": body.tier, "category": body.category,
        "headline": body.headline, "summary": body.summary, "stance": body.stance,
        "impact": body.impact, "confidence": 0.5, "eventTs": event_ts, "capturedTs": now,
        "expiresTs": intel_gate.expires_for_tier(tier=body.tier, event_ts=event_ts,
                                                 captured_ts=now, pinned=body.pinned,
                                                 limits=limits),
        "status": "active", "pinned": body.pinned, "userEdited": True,
        "undated": event_ts <= 0, "isScheduled": event_ts > now, "origin": "user",
        "sources": sources, "domainCount": len({s["domain"] for s in sources if s["domain"]}),
        "dedupKey": intel_gate.dedup_key(body.headline, [s["url"] for s in sources]),
    }
    ids = await repo.upsert_intel_items(session, [payload])
    intel_runtime.invalidate(instId)
    item = await repo.get_intel_item(session, ids[0])
    return {"item": item}


async def _load_item_or_404(session: AsyncSession, item_id: int) -> dict:
    item = await repo.get_intel_item(session, item_id)
    if item is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "情报条目不存在"})
    return item


def _expires_for(item: dict, patch: dict, limits) -> int:
    """按 patch 之后的 `tier` / `pinned` 重算寿命（不变式 I-3 在 REST 侧的落点）。"""
    return intel_gate.expires_for_tier(
        tier=str(patch.get("tier") or item["tier"]),
        event_ts=int(item["eventTs"]), captured_ts=int(item["capturedTs"]),
        pinned=bool(patch["pinned"] if "pinned" in patch else item["pinned"]),
        limits=limits)


@router.patch("/intel/items/{itemId}")
async def patch_intel_item(itemId: int, body: IntelItemPatchIn,
                           session: AsyncSession = Depends(get_session),
                           settings: Settings = Depends(get_settings)) -> dict:
    """编辑 / 置顶 / 静音 / 移层。任何触及 `tier` 或 `pinned` 的改动都在同一事务内重算寿命。"""
    _require_ai_enabled(settings)
    item = await _load_item_or_404(session, itemId)
    await _require_known_inst(session, item["instId"])
    derived = await intel_service.resolve_intel_settings(session, settings)
    patch = body.model_dump(exclude_none=True)
    limits = tax.limits_from_settings(derived)
    updated = await repo.patch_intel_item(
        session, itemId, patch,
        expires_ts=(_expires_for(item, patch, limits)
                    if ("tier" in patch or "pinned" in patch) else None))
    intel_runtime.invalidate(item["instId"])
    return {"item": updated}


@router.post("/intel/items/{itemId}/move")
async def move_intel_item(itemId: int, body: IntelMoveIn,
                          session: AsyncSession = Depends(get_session),
                          settings: Settings = Depends(get_settings)) -> dict:
    """移层（`tier` 的第二个写点；与 `PATCH` 共用同一条重算路径）。"""
    _require_ai_enabled(settings)
    item = await _load_item_or_404(session, itemId)
    await _require_known_inst(session, item["instId"])
    derived = await intel_service.resolve_intel_settings(session, settings)
    limits = tax.limits_from_settings(derived)
    patch = {"tier": body.tier}
    updated = await repo.patch_intel_item(session, itemId, patch,
                                          expires_ts=_expires_for(item, patch, limits))
    intel_runtime.invalidate(item["instId"])
    return {"item": updated}


@router.delete("/intel/items/{itemId}", status_code=204)
async def delete_intel_item(itemId: int, session: AsyncSession = Depends(get_session),
                            settings: Settings = Depends(get_settings)) -> None:
    """物理删除（**唯一**的硬删路径；自动清理一律走软删 `archived`）。"""
    _require_ai_enabled(settings)
    item = await _load_item_or_404(session, itemId)
    await _require_known_inst(session, item["instId"])
    await repo.delete_intel_item(session, itemId)
    intel_runtime.invalidate(item["instId"])


@router.post("/intel/{instId}/seen")
async def mark_intel_seen(instId: str, body: IntelSeenIn | None = None,
                          session: AsyncSession = Depends(get_session),
                          settings: Settings = Depends(get_settings)) -> dict:
    """标记已读：未读徽标的真值落 `ai_intel_watch.last_seen_ts`（评审 P1-2）。

    **服务端算 `unread`** 而不是前端 `localStorage`：后者换台机器、重装桌面端即失效，且与
    「服务端下发 summary」的架构相悖。
    """
    _require_ai_enabled(settings)
    await _require_known_inst(session, instId)
    await repo.mark_intel_seen(session, instId, ts=(body.ts if body else None))
    intel_runtime.invalidate(instId)
    # 走 `_summary` 而不是直接 `read_summary`：后者算不出 `inFlight` / 本轮 `degrade`
    # （库里没有落点），弹窗拿到这份回执后会把一个正在降级的标的画成一切正常。
    return {"summary": await _summary(session, instId, settings)}
