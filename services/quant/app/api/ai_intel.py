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
from ..ai_auto.intel import corpus as intel_corpus
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
from .schemas_intel_closed_loop import (
    IntelCitationListOut,
    IntelFeedbackIn,
    IntelFeedbackListOut,
    IntelFeedbackPreviewOut,
    IntelFeedbackWriteOut,
    IntelImpactOut,
    IntelProviderHealthOut,
    IntelQueuedOut,
    IntelSourceListOut,
)
from .schemas_intel_evidence import IntelDocumentHealthOut, IntelEvidenceOut

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


async def _effective_limits(session: AsyncSession, settings: Settings):
    """端点侧的**生效** `IntelLimits`（库内行叠加到 `Settings` 之后）。

    与运行时读的是同一条通路（`resolve_intel_settings` → `limits_from_settings`）：两处各读一份
    的后果是「面板上关了，可这一页还按开着算」。
    """
    derived = await intel_service.resolve_intel_settings(session, settings)
    return tax.limits_from_settings(derived)


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
    # ★ 传**派生副本**而不是进程级 `Settings`：`read_summary` 会读 `dailyCap` 与
    # actionability 的 `efficacy` 块，而这两样都住在 `app_settings` 里、由面板改写。传原始
    # `Settings` 的后果就是 `_effective_limits` 那段注释写的那一句——「面板上关了，可这一页
    # 还按开着算」，而且 WS 通路（`build_snapshot` 用的是 `deps.settings`，本来就是派生的）
    # 与 REST 通路会给出两个不同的答案，两边都不报错。
    derived = await intel_service.resolve_intel_settings(session, settings)
    fresh = await intel_service.read_summary(
        session, inst_id, settings=derived,
        provider_configured=intel_runtime.runtime_for(inst_id) is not None)
    if cached is None:
        return fresh
    return {**fresh, **{key: cached[key] for key in _RUNTIME_ONLY_KEYS if key in cached}}


# ⚠️ **字面量路径必须声明在 `/intel/{instId}` 之前**：FastAPI 按注册顺序匹配，
# `GET /intel/feedback` 若排在后面会被 `{instId}` 吃掉（`instId="feedback"`），
# 症状是一次 500 而不是 404 —— 路由被静默劫持是本仓库容易漏掉的一类接线错误。
# ---------------- 轴 D：信源治理（closed-loop §4.1 #13-#17） ----------------
@router.get("/intel/feedback", response_model=IntelFeedbackListOut)
async def read_intel_feedback(instId: str | None = None,
                              session: AsyncSession = Depends(get_session)) -> dict:
    """黑白名单清单（`instId` 缺省 = 全部：全局 + 每个标的）。

    **不设总闸**：看自己拉黑过谁不动任何资金，与 `GET /intel/{instId}` 同一条纪律。
    """
    return {"rows": await repo.list_intel_feedback(session, instId or "")}


async def _blocked_scope(session: AsyncSession, body: IntelFeedbackIn) -> set[str]:
    """本次拉黑之后**该作用域内**的完整黑名单（存量扫除与 preview 共用同一个口径）。"""
    domains = set(await repo.list_intel_blocked_domains(session, body.instId or ""))
    domains.add(body.key.strip().lower())
    return domains


@router.post("/intel/feedback", response_model=IntelFeedbackWriteOut)
async def write_intel_feedback(body: IntelFeedbackIn,
                               session: AsyncSession = Depends(get_session),
                               settings: Settings = Depends(get_settings)) -> dict:
    """记一条来源取舍；`block` 且存量扫除单键开着 ⇒ **同事务**扫除存量并返回归档条数。

    扫除的判据是「`sources` 非空且**每一个** source 的 domain 都在黑名单里」——只要还有一个
    干净来源，条目就留着（G-9②的另一半）。受保护条目（置顶 / 手改）跳过。
    """
    _require_ai_enabled(settings)
    if body.instId:
        await _require_known_inst(session, body.instId)
    key = body.key.strip().lower()
    # ★ 存量扫除单键必须读**运行时真正会读到的那一份**（`resolve_intel_settings` 的产物），
    # 而不是进程 env 上的 `Settings`：这个键露在 `PUT /settings/intel` 上、存在 `app_settings`
    # 表里，读进程副本就等于「面板上关得掉、系统永远照扫」——正是评审 P0-5 那类假开关。
    derived = await intel_service.resolve_intel_settings(session, settings)
    await repo.set_intel_feedback(session, key=key, action=body.action,
                                  inst_id=body.instId or "", commit=False)
    archived = 0
    if body.action == "block" and derived.ai_trade_intel_blocked_sweep_enabled:
        archived = await repo.sweep_blocked_source_items(
            session, body.instId or "", await _blocked_scope(session, body), commit=False)
    await session.commit()
    _invalidate_scope(body.instId or "")
    return {"ok": True, "archived": archived}


@router.post("/intel/feedback/preview", response_model=IntelFeedbackPreviewOut)
async def preview_intel_feedback(body: IntelFeedbackIn,
                                 session: AsyncSession = Depends(get_session)) -> dict:
    """给确认框算受影响条数（**纯读，绝不写库**，§4.1 #15b）。

    `trust` 恒回 0：它不归档任何东西。
    """
    if body.action != "block":
        return {"affected": 0}
    return {"affected": await repo.count_items_all_sources_blocked(
        session, body.instId or "", await _blocked_scope(session, body))}


@router.delete("/intel/feedback", status_code=204)
async def delete_intel_feedback(scope: str = Query(default="domain"),
                                key: str = Query(min_length=1),
                                instId: str = Query(default=""),
                                session: AsyncSession = Depends(get_session),
                                settings: Settings = Depends(get_settings)) -> None:
    """撤销一条来源取舍。

    **不复活**任何条目（G-9③）：归档是一次带理由的状态迁移，自动复活会让用户在两次点击之间
    看到条目数跳来跳去。恢复路径是「已归档」页签里那颗已经存在的恢复按钮。
    """
    _require_ai_enabled(settings)
    if scope != "domain":
        raise HTTPException(422, detail={"code": "intel_bad_scope",
                                         "message": "目前只支持 domain 作用域"})
    await repo.clear_intel_feedback(session, key=key.strip().lower(), inst_id=instId or "")
    _invalidate_scope(instId or "")


def _invalidate_scope(inst_id: str) -> None:
    """让受影响标的重算快照。作用域为空（全局）⇒ **全部**已装配标的都要重算。

    少这一步，用户拉黑之后决策 prompt 里还是旧的那一份，而**这件事不会报任何错**。
    """
    if inst_id:
        intel_runtime.invalidate(inst_id)
        return
    for runtime in intel_runtime.all_runtimes():
        for member in runtime.members:
            intel_runtime.invalidate(member)


@router.get("/intel/settings/health", response_model=IntelProviderHealthOut)
async def read_intel_provider_health(
        limit: int = Query(default=20, ge=1, le=50),
        session: AsyncSession = Depends(get_session)) -> dict:
    """最近 N 轮的检索源成败聚合 + 熔断剩余秒（§4.1 #16）。

    **不新建任何表、不新建任何计数器**：`ai_intel_rounds.search_provider` 已经逐轮记着实际用的
    slug，熔断剩余秒来自进程内 breaker（没有已装配的 runtime ⇒ 0，不是猜一个数）。
    """
    rounds: list[dict] = []
    for runtime in intel_runtime.all_runtimes():
        for member in runtime.members:
            rounds.extend(await repo.list_intel_rounds(session, member, limit=limit))
    rounds.sort(key=lambda row: int(row.get("startedTs") or 0), reverse=True)
    rounds = rounds[:limit]
    provider = next((str(row.get("searchProvider") or "") for row in rounds
                     if row.get("searchProvider")), "")
    breaker_left = 0
    for runtime in intel_runtime.all_runtimes():
        search = getattr(runtime.deps, "search", None)
        if search is not None:
            breaker_left = max(breaker_left, int(search.breaker.remaining_s()))
    return {
        "provider": provider,
        "rounds": len(rounds),
        "ok": sum(1 for row in rounds if row.get("status") == "ok"),
        "failed": sum(1 for row in rounds if row.get("status") == "failed"),
        "degraded": sum(1 for row in rounds if row.get("status") == "degraded"),
        "breakerRemainingS": breaker_left,
    }


# ---------------- 轴 C：证据可读（evidence-grounding §4.1 N-1 / N-2） ----------------
# ⚠️ `/intel/documents/health` 是**字面量路径**，必须声明在 `/intel/{instId}` 之前：FastAPI 按
# 注册顺序匹配，排在后面会被 `{instId}` 吃掉（`instId="documents"`），症状是一次 500 而不是 404。
@router.get("/intel/documents/health", response_model=IntelDocumentHealthOut)
async def read_intel_document_health(
        session: AsyncSession = Depends(get_session),
        settings: Settings = Depends(get_settings)) -> dict:
    """正文缓存的只读读数（设置卡「原文缓存」那一行）。

    `readerEnabled` 取**运行时真正会读到的那一份**（库内行叠加到 `Settings` 之后），与设置卡
    上那个开关同源——两处各读一份的后果是「关掉了但这一行还说开着」。
    """
    derived = await intel_service.resolve_intel_settings(session, settings)
    health = await repo.document_health(session)
    return {**health,
            "readerEnabled": bool(getattr(derived, "ai_trade_intel_reader_enabled", True))}


@router.get("/intel/items/{itemId}/evidence", response_model=IntelEvidenceOut)
async def read_intel_evidence(itemId: int,
                              session: AsyncSession = Depends(get_session),
                              settings: Settings = Depends(get_settings)) -> dict:
    """一条情报的全部原文引证 + 它那几个来源当前的缓存状态（证据抽屉的**唯一**读点）。

    **不设总闸**：读自己库里的证据不动任何资金（与 `GET /intel/{instId}` 同一条纪律）。
    条目不存在 ⇒ 404；**无引文 ⇒ 200 + 空数组**（不是 404——「这条没有引证」是一个正常状态，
    把它做成 404 会让前端把一次正常展开画成一次请求失败）。
    """
    item = await repo.get_intel_item(session, itemId)
    if item is None:
        raise HTTPException(404, detail={"code": "intel_item_not_found",
                                         "message": f"情报 #{itemId} 不存在"})
    derived = await intel_service.resolve_intel_settings(session, settings)
    keys = [intel_corpus.document_key(str(source.get("url") or ""))
            for source in (item.get("sources") or []) if source.get("url")]
    return {"itemId": int(itemId),
            "quoteMode": tax.resolve_quote_mode(derived),
            "quotes": await repo.list_item_quotes(session, itemId),
            "docs": await repo.list_documents_for_urls(session, keys)}


@router.get("/intel/{instId}/sources", response_model=IntelSourceListOut)
async def read_intel_sources(instId: str,
                             session: AsyncSession = Depends(get_session),
                             settings: Settings = Depends(get_settings)) -> dict:
    """当前库存的域名聚合 + **每个域名的核查信誉**（§4.1 #17 + actionability 轴 B）。

    「证实 / 证伪 / 准确率」三列是拉黑决策**唯一需要**的数据——上一轮做好了拉黑功能，用户却
    只能看着 `coindesk.com` 与某个内容农场并排列在这一页上，手里没有任何数据可以决定该拉黑
    哪一个。样本不足时 `accuracy` 下发 `null`（**不是 0**）。
    """
    rows = await repo.list_intel_source_rows(session, instId)
    limits = await _effective_limits(session, settings)
    eff = await intel_service.read_efficacy(session, instId, limits=limits, now=time.time())
    reputation = intel_service.source_reputation_wire(eff, limits=limits)
    return {"rows": [{**row, **_reputation_of(reputation, row["domain"])} for row in rows]}


def _reputation_of(reputation: dict[str, dict], domain: str) -> dict:
    """一个域名的四个信誉键；没有任何判定样本时 ⇒ 三个 0 + `accuracy=None`。"""
    stat = reputation.get(domain) or {}
    return {"confirmed": int(stat.get("confirmed", 0)),
            "refuted": int(stat.get("refuted", 0)),
            "unresolved": int(stat.get("unresolved", 0)),
            "accuracy": stat.get("accuracy")}


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
    limits = await _effective_limits(session, settings)
    # actionability 轴 C：矛盾对是**当前库存的函数**，不落库（持久化只会带来陈旧）。本端点
    # **未挂** `response_model`（模块 docstring 第一条纪律），故可以直接加键。
    _pairs, conflicts = await intel_service.load_conflicts(
        session, instId, limits=limits, now=time.time())
    return {"instId": instId, "summary": await _summary(session, instId, settings),
            "tiers": tiers, "archivedTotal": counts["archived"],
            "mutedTotal": counts["muted"], "conflicts": conflicts}


@router.get("/intel/{instId}/alerts")
async def read_intel_alerts(instId: str,
                            limit: int = Query(default=50, ge=1, le=200),
                            before: int | None = None,
                            session: AsyncSession = Depends(get_session)) -> dict:
    """情报告警台账（按 `id` 降序游标分页，actionability §4.1 #1）。

    **含「没告警」的行**：空轮也写一行，因此「今天为什么一次都没告警」永远有答案——那些行的
    `fired=False` 且带一个抑制码与它的中文文案。

    游标是 `id` 而不是时间戳：同一秒里的多行用时间戳翻页会漏行（沿用 `/citations` 的既有裁决）。

    ⚠️ **本端点刻意不挂 `response_model`**（评审 P2-9）：告警行 wire 上有 `reasons` 这个自由
    形状的判据明细字典，挂模型只会得到「加了新判据、界面上看不到、也不报错」。`/citations`
    挂着模型是那一轮的例外，本轮跟随模块 docstring 第一条纪律而不是跟随它。

    **不设总闸**：看自己的告警历史不动任何资金（与 `GET /intel/{instId}` 同一条纪律）。
    """
    rows, has_more = await repo.list_intel_alerts(session, inst_id=instId, limit=limit,
                                                  before=before)
    return {"alerts": rows, "hasMore": has_more}


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
    # 「我不同意」（closed-loop §4.1 #22）：它不是 `patch_intel_item` 的一个普通字段——
    # 清 verdict 的同时**必须**置 `user_edited`（兼任判定锁）且**绝不清 `verify_count`**。
    # 清了它，这条目下一轮就会回到候选池最前面被重新判成 refuted，撤销出口当场变成旋转门
    # （评审 P0-4）。故它单独走一条写路径，且要在 `patch` 交给仓库层之前摘掉。
    clear_verdict = bool(patch.pop("clearVerdict", False))
    limits = tax.limits_from_settings(derived)
    updated = item
    if patch:
        updated = await repo.patch_intel_item(
            session, itemId, patch,
            expires_ts=(_expires_for(item, patch, limits)
                        if ("tier" in patch or "pinned" in patch) else None))
    if clear_verdict:
        updated = await repo.clear_intel_verdict(session, itemId)
    intel_runtime.invalidate(item["instId"])
    return {"item": updated}


@router.post("/intel/{instId}/verify", status_code=202, response_model=IntelQueuedOut)
async def trigger_verify(instId: str, session: AsyncSession = Depends(get_session),
                         settings: Settings = Depends(get_settings)) -> dict:
    """手动触发一轮核查（closed-loop §4.1 #20，**排队即返回**）。"""
    _require_ai_enabled(settings)
    await _require_known_inst(session, instId)
    runtime = _require_runtime(instId)
    if runtime.lock.locked():
        raise HTTPException(409, detail={"code": "intel_busy",
                                         "message": "该标的已有一轮情报正在进行中"})
    intel_scheduler.schedule(runtime.run_verify(instId))
    return {"queued": True}


@router.post("/intel/items/{itemId}/verify", status_code=202, response_model=IntelQueuedOut)
async def trigger_item_verify(itemId: int, session: AsyncSession = Depends(get_session),
                              settings: Settings = Depends(get_settings)) -> dict:
    """只核查这一条（closed-loop §4.1 #21）。

    **显式绕过 `plan_verification` 的全部候选过滤**（`origin` / `verify_count` / `user_edited` /
    `verify_min_age_h` 一概不看，评审 P2-11）——它是一条**人类显式指令**，不是自动池的一员。
    这也是用户手写条目与「我不同意」过的条目**唯一**的核查途径。仍受日配额与 `_require_runtime`
    约束（额度是成本闸，手动触发不该能绕过它）。
    """
    _require_ai_enabled(settings)
    item = await _load_item_or_404(session, itemId)
    inst_id = item["instId"]
    await _require_known_inst(session, inst_id)
    runtime = _require_runtime(inst_id)
    if runtime.lock.locked():
        raise HTTPException(409, detail={"code": "intel_busy",
                                         "message": "该标的已有一轮情报正在进行中"})
    watch = await repo.load_intel_watch(session, inst_id) or {}
    if intel_service.daily_used(watch) >= int(settings.ai_trade_intel_daily_round_cap):
        raise HTTPException(429, detail={
            "code": "intel_budget_exhausted",
            "message": tax.degrade_message("budget_exhausted")})
    intel_scheduler.schedule(runtime.run_verify(inst_id, only_item_id=itemId))
    return {"queued": True}


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


# ---------------- 轴 B：影响力与引用明细（closed-loop §4.1 #18-#19） ----------------
@router.get("/intel/{instId}/impact", response_model=IntelImpactOut)
async def read_intel_impact(instId: str,
                            limit: int = Query(default=10, ge=1, le=50),
                            session: AsyncSession = Depends(get_session),
                            settings: Settings = Depends(get_settings)) -> dict:
    """影响力页签的全部读数（§4.1 #18 + actionability 轴 B 的「战绩与信誉」块）。

    `totals.netPnlUsd` 是**关联**净额：这些数字是**共现**统计，不是因果归因——一笔交易通常
    同时采信了多条证据（R-1）。这条口径必须一路带到 UI 文案上。

    ⚠️ 本端点**挂着** `response_model=IntelImpactOut`（E-19 点名的那一个例外）：新增的
    `efficacy` 键必须同时出现在模型里，否则被**静默丢弃**（G-23 从 HTTP 响应体断言，
    不是从仓库函数的返回值）。
    """
    report = await repo.read_intel_impact(session, instId, limit=limit)
    limits = await _effective_limits(session, settings)
    eff = await intel_service.read_efficacy(session, instId, limits=limits, now=time.time())
    return {**report, "efficacy": intel_service.efficacy_wire(eff, limits=limits)}


@router.get("/intel/{instId}/citations", response_model=IntelCitationListOut)
async def read_intel_citations(instId: str, itemId: int | None = None,
                               limit: int = Query(default=50, ge=1, le=200),
                               before: int | None = None,
                               session: AsyncSession = Depends(get_session)) -> dict:
    """引用明细（游标分页，按 `id` 降序，§4.1 #19）。

    游标是 `id` 而不是时间戳：同一秒里的多条引用用时间戳翻页会漏行。
    """
    rows, has_more = await repo.list_intel_citations(
        session, inst_id=instId, item_id=itemId, limit=limit, before=before)
    return {"rows": rows, "hasMore": has_more}
