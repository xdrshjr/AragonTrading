"""路由：health / instruments / candles (spec §9.1)。

真实数据硬约束 (C1)：OKX 不可达时抛 OKXError（由 main 统一转 5xx 显式错误），
严禁静默回退到假数据；缓存回退仅在 ALLOW_CACHED_FALLBACK=true 时启用。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import repositories as repo
from ..db.session import get_session
from ..okx.link_state import get_link_registry
from ..okx.rest_client import OKXError, get_rest_client

router = APIRouter(tags=["market"])
logger = logging.getLogger(__name__)

# 仅 OKX 公共行情支持的可枚举标的类型；非法值在边界处一次性拒绝（C1 显式错误）。
_ALLOWED_INST_TYPES = {"SPOT", "SWAP", "FUTURES"}


def _is_sqlite_locked(exc: OperationalError) -> bool:
    return "database is locked" in str(exc).lower()


@router.get("/health")
async def health() -> dict:
    """健康检查：OKX 可达性 + 时延 + 最近成功取数时间 + WS 端点是否配置 (F2)。"""
    rest = get_rest_client()
    okx_up = await rest.health()
    # getattr 兜底：使旧/测试用假客户端无需实现这些属性也不致 500
    latency = getattr(rest, "last_latency_ms", None) if okx_up else None
    last_ok_ts = getattr(rest, "last_ok_ts", None)
    settings = get_settings()
    return {
        "status": "ok",
        "okx": "up" if okx_up else "down",
        "okxLatencyMs": latency,
        "lastOkTs": last_ok_ts,
        "wsConfigured": bool(settings.okx_ws_public and settings.okx_ws_business),
        # F2：缓存回退开关是否启用——前端据此预知"回退可能发生"（仅信息性，不放宽硬约束）
        "cachedFallbackEnabled": settings.allow_cached_fallback,
        "maxPaperSessions": settings.max_paper_sessions,
        # 加法键（okx-link-resilience §4.3）：本端点**未声明** `response_model`，故新键不会被静默
        # 剔除。只带摘要两项，详情走 `/api/okx/connectivity`——顶栏 15s 轮询不该顺带拖一份全量快照。
        "link": _link_summary(),
    }


def _link_summary() -> dict:
    """链路摘要（`overall` 只由**行情面**决定；账户面的问题在 summary 里说，不冒充行情断线）。"""
    snap = get_link_registry().snapshot()
    return {"overall": snap["overall"], "summary": snap["summary"]}


@router.get("/instruments")
async def get_instruments(instType: str = Query("SWAP")) -> list[dict]:
    """按类型返回该 instType 全量标的（带 24h 行情、按成交额降序）。

    `instType` 仅接受 `SPOT|SWAP|FUTURES`，非法值→400（main 全局处理器包成 {error:{...}}）。
    """
    if instType not in _ALLOWED_INST_TYPES:
        raise HTTPException(
            400,
            detail={
                "code": "BAD_INST_TYPE",
                "message": f"instType 必须是 {sorted(_ALLOWED_INST_TYPES)} 之一，收到 {instType!r}",
            },
        )
    instruments = await get_rest_client().get_instruments(instType)
    return [i.model_dump() for i in instruments]


@router.get("/candles")
async def get_candles(
    instId: str,
    response: Response,
    bar: str = "1H",
    limit: int = Query(300, ge=1, le=1000),
    before: int | None = None,    # 秒级 UTC (C2)
    after: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """历史 K 线。响应头标注数据来源（F2，body 结构不变、向后兼容）：

    - 真实实时取数 -> ``X-Aragon-Data-Source: live``
    - 缓存回退（仅 ALLOW_CACHED_FALLBACK=true 且命中缓存）-> ``X-Aragon-Data-Source: cache`` + ``X-Aragon-Stale: 1``
    - OKX 不可达且未开回退 / 无缓存 -> 维持显式 5xx（严禁假数据，C1）
    """
    rest = get_rest_client()
    try:
        candles = await rest.get_candles(instId, bar, limit, before=before, after=after)
    except OKXError:
        settings = get_settings()
        if settings.allow_cached_fallback:
            cached = await repo.get_cached_candles(session, instId, bar, limit)
            if cached:
                # ⚠️ 缓存样例（真实 OKX 历史，但非最新）：经响应头诚实标注，前端醒目提示"非实时"（F2）
                response.headers["X-Aragon-Data-Source"] = "cache"
                response.headers["X-Aragon-Stale"] = "1"
                return cached
        raise  # 由 main 的异常处理器转为 5xx 显式错误态

    try:
        await repo.upsert_candles(session, instId, bar, candles)
    except IntegrityError:
        await session.rollback()
        logger.warning(
            "Candle cache write skipped after integrity conflict: instId=%s bar=%s "
            "limit=%s before=%s after=%s",
            instId,
            bar,
            limit,
            before,
            after,
            exc_info=True,
        )
    except OperationalError as exc:
        if not _is_sqlite_locked(exc):
            raise
        await session.rollback()
        logger.warning(
            "Candle cache write skipped after SQLite lock: instId=%s bar=%s "
            "limit=%s before=%s after=%s",
            instId,
            bar,
            limit,
            before,
            after,
            exc_info=True,
        )

    response.headers["X-Aragon-Data-Source"] = "live"
    return [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol} for c in candles]
