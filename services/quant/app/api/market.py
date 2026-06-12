"""路由：health / instruments / candles (spec §9.1)。

真实数据硬约束 (C1)：OKX 不可达时抛 OKXError（由 main 统一转 5xx 显式错误），
严禁静默回退到假数据；缓存回退仅在 ALLOW_CACHED_FALLBACK=true 时启用。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import repositories as repo
from ..db.session import get_session
from ..okx.rest_client import OKXError, get_rest_client

router = APIRouter(tags=["market"])


@router.get("/health")
async def health() -> dict:
    """健康检查：OKX 可达性 + 时延 + 最近成功取数时间 + WS 端点是否配置 (F2)。"""
    rest = get_rest_client()
    okx_up = await rest.health()
    # getattr 兜底：使旧/测试用假客户端无需实现这些属性也不致 500
    latency = getattr(rest, "last_latency_ms", None) if okx_up else None
    last_ok_ts = getattr(rest, "last_ok_ts", None)
    return {
        "status": "ok",
        "okx": "up" if okx_up else "down",
        "okxLatencyMs": latency,
        "lastOkTs": last_ok_ts,
        "wsConfigured": bool(get_settings().okx_ws_public),
    }


@router.get("/instruments")
async def get_instruments(instType: str = Query("SWAP")) -> list[dict]:
    instruments = await get_rest_client().get_instruments(instType)
    return [i.model_dump() for i in instruments]


@router.get("/candles")
async def get_candles(
    instId: str,
    bar: str = "1H",
    limit: int = Query(300, ge=1, le=1000),
    before: int | None = None,    # 秒级 UTC (C2)
    after: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rest = get_rest_client()
    try:
        candles = await rest.get_candles(instId, bar, limit, before=before, after=after)
        await repo.upsert_candles(session, instId, bar, candles)
        return [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol} for c in candles]
    except OKXError:
        settings = get_settings()
        if settings.allow_cached_fallback:
            cached = await repo.get_cached_candles(session, instId, bar, limit)
            if cached:
                return cached  # ⚠️ DEV 缓存样例（前端须显著标注"非实时"）
        raise  # 由 main 的异常处理器转为 5xx 显式错误态
