"""路由：AI 自动交易的交易日志管理台（ai-trade-audit-journal §四）。

10 个端点 = 9 个只读 + 1 个剪枝。**总闸 `AI_TRADE_JOURNAL_UI_ENABLED=false` ⇒ 整个路由不注册**
（全部 404），装配在 `main.py`——沿用 `truth_sources` 的先例：注册一个恒回空的端点等于
「装了但什么都不说」，而那是审计系统最不该有的形状。

两条与 `api/truth_sources.py` 同源的纪律：

- **带嵌套领域对象的读端点不声明 `response_model`**：声明之后加法键会被 FastAPI 静默剔除。
- **列表端点一律 keyset 分页**（`beforeId` 游标），`total` 恒为 `null`。

⚠️ 每一个经 `/api/py/*` 的请求都受 `apps/web/next.config.mjs` 的
`experimental.proxyTimeout: 300000` 约束（300 秒）。列表 / 详情 / 统计都是毫秒级，唯一贴近
这堵墙的是 `/export`，其保护是服务端的墙钟预算（§2.5.1 加强一）。
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_auto.journal import export_audit, export_sft, linking, retention, service
from ..ai_auto.journal.export_audit import ExportBudget
from ..ai_auto.journal.export_sft import CONTEXT_MODES, SftContext, SftOptions
from ..config import Settings, get_settings
from ..db import ai_journal_repositories as journal_repo
from ..db.session import get_session
from .schemas_journal import EXPORT_FORMATS, ExportPreviewIn, PruneIn, PruneOut

router = APIRouter(prefix="/ai-trade/journal", tags=["ai-trade-journal"])
logger = logging.getLogger(__name__)

# 列表页每页条数的上界。200 是「一屏翻不完但一次查询仍是毫秒级」的折中。
MAX_PAGE_LIMIT = 200


def _decision_filters(  # noqa: PLR0913 —— FastAPI 的 Query 声明只能逐个写，这不是业务形参
    groupId: str | None = None, instId: str | None = None, bar: str | None = None,
    sinceTs: int | None = None, untilTs: int | None = None, action: str | None = None,
    trigger: str | None = None, executedOnly: bool = False, degradedOnly: bool = False,
    gateVetoedOnly: bool = False, q: str | None = Query(default=None, max_length=64),
    beforeId: int | None = None,
) -> dict:
    """把 12 个 query 参数收成一份过滤 dict（`Depends` 装配，端点签名保持在 3 个形参内）。

    `q` 的长度上界由 `Query(max_length=64)` 在**框架层**执行 ⇒ 超长即 422。它进的是 SQL
    `LIKE`，无界的关键字会把十万行上的扫描拖成分钟级。
    """
    return {"groupId": groupId, "instId": instId, "bar": bar, "sinceTs": sinceTs,
            "untilTs": untilTs, "action": action, "trigger": trigger,
            "executedOnly": executedOnly, "degradedOnly": degradedOnly,
            "gateVetoedOnly": gateVetoedOnly, "q": q, "beforeId": beforeId}


@router.get("/sessions")
async def read_sessions(limit: int = Query(default=30, ge=1, le=100),
                        cursorTs: int | None = None,
                        session: AsyncSession = Depends(get_session)) -> dict:
    """会话选择器数据源。

    与既有 `/ai-trade/history` 的区别是硬性的：后者**刻意排除当前运行组**，而日志台必须能看
    正在跑的那一个——「刚才那一轮为什么这么决策」问的恰恰是还在跑的那个会话。
    """
    return await journal_repo.list_journal_sessions(session, limit=limit, cursor_ts=cursorTs)


@router.get("/decisions")
async def read_decisions(filters: dict = Depends(_decision_filters),
                         limit: int = Query(default=50, ge=1, le=MAX_PAGE_LIMIT),
                         session: AsyncSession = Depends(get_session)) -> dict:
    """决策轮次列表（keyset 分页；`total` 恒为 `null`，见模块 docstring）。"""
    return await service.list_decisions(session, filters, limit)


@router.get("/decisions/{row_id}")
async def read_decision(row_id: int, session: AsyncSession = Depends(get_session),
                        settings: Settings = Depends(get_settings)) -> dict:
    """单条决策的七段详情。"""
    out = await service.get_decision(session, row_id, settings)
    if out is None:
        raise HTTPException(status_code=404, detail={
            "code": "JOURNAL_DECISION_NOT_FOUND", "message": "该决策留痕不存在或已被剪枝"})
    return out


@router.get("/decisions/{row_id}/prompt")
async def read_decision_prompt(row_id: int,
                               session: AsyncSession = Depends(get_session)) -> dict:
    """按存档 `payload` 重建提示词并与存档 sha 比对。

    `shaMatch=false` **不是错误**（仍返回 200）：重建出来的文本仍有参考价值。但前端必须显著
    告知它与当时喂给模型的不完全相同——这句话直接决定用户会不会拿一批假样本去训练。
    """
    out = await service.rebuild_prompt(session, row_id)
    if out is None:
        raise HTTPException(status_code=404, detail={
            "code": "JOURNAL_DECISION_NOT_FOUND", "message": "该决策留痕不存在或已被剪枝"})
    if out.get("error") == "payload_absent":
        raise HTTPException(status_code=409, detail={
            "code": "JOURNAL_PAYLOAD_ABSENT",
            "message": "该轮以 minimal 档采集，未留存上下文，无法重建提示词。"})
    return out


@router.get("/events")
async def read_events(groupId: str | None = None, instId: str | None = None,
                      level: str | None = None, category: str | None = None,
                      sinceTs: int | None = None, untilTs: int | None = None,
                      beforeId: int | None = None,
                      limit: int = Query(default=50, ge=1, le=MAX_PAGE_LIMIT),
                      session: AsyncSession = Depends(get_session)) -> dict:
    """系统事件列表。"""
    filters = {"groupId": groupId, "instId": instId, "level": level, "category": category,
               "sinceTs": sinceTs, "untilTs": untilTs, "beforeId": beforeId}
    return await service.list_events(session, filters, limit)


@router.get("/fills")
async def read_fills(filters: dict = Depends(_decision_filters),
                     limit: int = Query(default=50, ge=1, le=MAX_PAGE_LIMIT),
                     session: AsyncSession = Depends(get_session)) -> dict:
    """成交与订单列表。每条带 `decisionRef`——**成交找得回决策**是审计的另一半。"""
    return await service.list_fills(session, filters, limit)


@router.get("/stats")
async def read_stats(session: AsyncSession = Depends(get_session),
                     settings: Settings = Depends(get_settings)) -> dict:
    """采集健康度与全部口径读数。**前端的每一个口径数字都来自这里，一个都不硬编码。**"""
    return await service.journal_stats(session, settings)


@router.post("/export/preview")
async def preview_export(body: ExportPreviewIn,
                         settings: Settings = Depends(get_settings)) -> dict:
    """导出预检：精确条数 + 九个 `skipped` 桶 + 预估体积 / 耗时 / 片数 + 解出的关联档位。

    `slices > 1` 时前端把一次「导出全部历史」变成**顺序下载 N 个文件**，每片一次独立请求、
    各自远在 300 秒之内（评审 RP0-2）。
    """
    _require_format(body.format)
    filters = body.scope.model_dump()
    budget = _budget(settings)
    ctx = await _sft_context(settings, body.sft.minLinkConfidence, filters)
    opts = _sft_options(body.sft, ctx)
    if body.format != "sft-jsonl":
        return await _audit_preview(filters, budget, ctx)
    return await export_sft.preview(filters, budget, opts, ctx)


# 审计两档的两个量级常数。它们是**估算**、只用来决定「切几片 / 提示多大」，
# 绝不参与任何截断判定（真正的终止条件是服务端的 `EXPORT_MAX_ROWS` 与墙钟预算）。
_AUDIT_BYTES_PER_ROW = 1200      # 扁平字段、无 payload 全文的一行的中位数体量
_AUDIT_SECONDS_PER_ROW = 0.0025  # 一行的关联 + 装配 + 序列化耗时（本机实测量级）


async def _audit_preview(filters: dict, budget: ExportBudget, ctx: SftContext) -> dict:
    """审计两档的预检：精确条数走一次 `COUNT`（导出预检是**唯一**允许 COUNT 的地方）。"""
    import math

    from ..db.session import get_sessionmaker
    async with get_sessionmaker()() as session:
        rows = await journal_repo.count_decision_rows(session, filters)
        estimated = rows * _AUDIT_SECONDS_PER_ROW
        slices = 1
        if budget.time_budget_s > 0:
            slices = max(1, math.ceil(estimated / budget.time_budget_s))
        cursors = await journal_repo.slice_boundary_ids(session, filters, slices)
    return {"rows": min(rows, budget.max_rows), "approxBytes": rows * _AUDIT_BYTES_PER_ROW,
            "overLimit": rows > budget.max_rows,
            "estimatedSeconds": round(estimated, 1), "slices": slices,
            "sliceCursors": cursors,
            "timeBudgetS": budget.time_budget_s,
            "resolvedMinLinkConfidence": ctx.resolved_min_link_confidence,
            "resolvedReason": ctx.resolved_reason,
            "skipped": dict.fromkeys(export_sft.SKIPPED_BUCKETS, 0)}


@router.get("/export")
async def export_journal(  # noqa: PLR0913 —— 同 `_decision_filters`：query 声明只能逐个写
    format: str = Query(default="audit-csv"),
    filters: dict = Depends(_decision_filters),
    excludeGateVetoed: bool = True, excludeDegraded: bool = True, onlySettled: bool = True,
    includeHold: bool = False, minLinkConfidence: str = "auto", contextMode: str = "full",
    part: int = Query(default=1, ge=1, le=999),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """流式导出（`audit-csv` / `audit-jsonl` / `sft-jsonl`）。

    三个终止条件取**先到者**：过滤耗尽 / `EXPORT_MAX_ROWS` / 墙钟预算。触顶即**干净收尾**并在
    `eof`（CSV 是 `#TRUNCATED` 尾注行）里给出 `nextBeforeId`——它就是下一片的游标。
    `part` 只影响文件名，不影响取数（游标由 `beforeId` 承载）。
    """
    _require_format(format)
    budget = _budget(settings)
    stamp = time.strftime("%Y%m%d-%H%M")
    suffix = f"-part{part:02d}" if part > 1 else ""
    meta = {"scope": {k: v for k, v in filters.items() if v not in (None, False)},
            "captureTier": settings.ai_trade_journal_capture_tier}
    if format == "audit-csv":
        stream = export_audit.stream_audit_csv(filters, budget)
        media, ext = export_audit.CSV_MEDIA_TYPE, "csv"
    elif format == "audit-jsonl":
        stream = export_audit.stream_audit_jsonl(filters, budget, meta)
        media, ext = export_audit.NDJSON_MEDIA_TYPE, "ndjson"
    else:
        ctx = await _sft_context(settings, minLinkConfidence, filters)
        opts = _sft_options_raw(excludeGateVetoed, excludeDegraded, onlySettled,
                                includeHold, minLinkConfidence, contextMode, ctx)
        stream = export_sft.stream_sft(filters, budget, opts, ctx, meta)
        media, ext = export_audit.NDJSON_MEDIA_TYPE, "ndjson"
    filename = f"aragon-ai-trade-journal-{format}-{stamp}{suffix}.{ext}"
    return StreamingResponse(stream, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store"})


@router.post("/prune", response_model=PruneOut)
async def prune(body: PruneIn, session: AsyncSession = Depends(get_session),
                settings: Settings = Depends(get_settings)) -> PruneOut:
    """手动剪枝（双闸 + 最老优先）。**绝不删除运行中 / 已暂停分组的行。**

    `dryRun` 默认 `True`：一个默认就删的删除端点是不可接受的。
    """
    stats = await retention.prune_with_session(session, settings, dry_run=body.dryRun)
    counts = {"decisions": stats["decisions"], "events": stats["events"]}
    return PruneOut(wouldDelete=counts if body.dryRun else None,
                    deleted=None if body.dryRun else counts,
                    protectedGroups=stats["protectedGroups"], prunedTs=stats["prunedTs"])


# ---------------------------------------------------------------- 内部装配

def _require_format(fmt: str) -> None:
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(status_code=422, detail={
            "code": "JOURNAL_FORMAT_INVALID",
            "message": f"导出格式必须是 {list(EXPORT_FORMATS)} 之一"})


def _budget(settings: Settings) -> ExportBudget:
    return ExportBudget(page_size=int(settings.ai_trade_journal_export_page_size),
                        max_rows=int(settings.ai_trade_journal_export_max_rows),
                        time_budget_s=float(settings.ai_trade_journal_export_time_budget_s))


async def _sft_context(settings: Settings, requested: str, filters: dict) -> SftContext:
    """`minLinkConfidence=auto` 的**服务端唯一**解出点；结果随预检与 manifest 一起下发。"""
    chain = bool(settings.ai_trade_journal_context_chain_enabled)
    has_client_id = False
    if not requested or requested == "auto":
        has_client_id = await export_sft.scope_has_client_order_id(filters)
    resolved = linking.resolve_min_link_confidence(
        requested, has_client_order_id=has_client_id)
    return SftContext(context_chain_enabled=chain,
                      resolved_min_link_confidence=resolved["value"],
                      resolved_reason=resolved["reason"])


def _sft_options(body, ctx: SftContext) -> SftOptions:
    return _sft_options_raw(body.excludeGateVetoed, body.excludeDegraded, body.onlySettled,
                            body.includeHold, body.minLinkConfidence, body.contextMode, ctx)


def _sft_options_raw(exclude_gate_vetoed: bool, exclude_degraded: bool, only_settled: bool,
                     include_hold: bool, min_link_confidence: str, context_mode: str,
                     ctx: SftContext) -> SftOptions:
    """把入参收成 `SftOptions`，并在总闸关闭时**强制**降级为 `lastTurnOnly`。

    总闸 `AI_TRADE_JOURNAL_CONTEXT_CHAIN_ENABLED=false` 时两列恒 NULL，`full` 根本重建不出来。
    此时**不静默按 full 走**（那会让每一条样本都掉进 `contextIncomplete`，用户看到的是一个
    没有解释的空文件），而是直接落到 `lastTurnOnly` 并让每条样本带 `contextTruncated=true`。
    """
    mode = context_mode if context_mode in CONTEXT_MODES else "full"
    if not ctx.context_chain_enabled:
        mode = "lastTurnOnly"
    return SftOptions(exclude_gate_vetoed=exclude_gate_vetoed,
                      exclude_degraded=exclude_degraded, only_settled=only_settled,
                      include_hold=include_hold,
                      min_link_confidence=min_link_confidence, context_mode=mode)
