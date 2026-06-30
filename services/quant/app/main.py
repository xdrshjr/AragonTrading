"""FastAPI 应用入口 (spec §8.3)。

挂载路由、CORS、启动/关闭钩子、OKXError -> 统一 5xx 错误体 (C1)。
启动：uvicorn app.main:app --port 18760
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .agent.manager import get_agent_manager
from .api import (
    accounts,
    agent,
    app_settings,
    factors,
    live_auto,
    market,
    paper,
    runs,
    strategies,
    trading,
    weight_models,
    workspace,
    ws_market,
)
from .db.session import dispose_engine, init_db
from .factors.weight_train_manager import get_weight_train_manager
from .live_auto.manager import get_live_auto_manager
from .okx.rest_client import OKXError, get_rest_client
from .paper.manager import get_paper_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("aragon")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("DB initialized; builtin strategies seeded.")
    await _backfill_companion_strategies()
    await _reconcile_weight_train_runs()
    sweeper = asyncio.create_task(_weight_train_sweeper())   # H-B6：周期回收过期终态作业（cancel-safe）
    yield
    sweeper.cancel()                                         # 关停先取消清扫协程（与 manager.shutdown 同段）
    await get_agent_manager().shutdown()
    await get_paper_manager().shutdown()
    await get_live_auto_manager().shutdown()
    await get_weight_train_manager().shutdown()
    await get_rest_client().aclose()
    await dispose_engine()


async def _backfill_companion_strategies() -> None:
    """启动后对历史无伴生的因子组合幂等补建伴生 factor_score 策略（factor-combo-strategy-sync，R6）。

    lifespan 不开 ``Depends(get_session)``，故用 ``get_sessionmaker()`` 开一次性 session；**整段
    ``try/except`` 包裹，补建失败仅 ``logger.warning`` 不阻断启动**（补建为非关键收敛，下次组合更新
    经 ``update_set`` 自愈）。延迟导入避免应用加载期与 factors/service 的层间硬依赖。
    """
    try:
        from .db.session import get_sessionmaker
        from .factors import service as factor_service

        async with get_sessionmaker()() as session:
            created = await factor_service.backfill_companions(session)
        if created:
            logger.info("backfilled %d companion factor_score strategies", created)
    except Exception as exc:  # noqa: BLE001 —— 启动补建非关键，绝不因它拖垮服务启动
        logger.warning("companion strategy backfill skipped: %s", exc)


async def _reconcile_weight_train_runs() -> None:
    """启动对账（weight-model-workspace R-04）：把上轮进程残留的 ``running`` 训练记录批量改判 ``failed``。

    仿 ``_backfill_companion_strategies`` 一次性 session；**整段 try/except 包裹，失败仅 warning 不阻断启动**
    （对账为非关键收敛，避免悬挂 running 让详情页假「训练中」）。
    """
    try:
        from .db import repositories as repo
        from .db.session import get_sessionmaker

        async with get_sessionmaker()() as session:
            reconciled = await repo.reconcile_dangling_weight_runs(session)
        if reconciled:
            logger.info("reconciled %d dangling weight-train runs to failed", reconciled)
    except Exception as exc:  # noqa: BLE001 —— 启动对账非关键，绝不因它拖垮服务启动
        logger.warning("weight-train run reconcile skipped: %s", exc)


async def _weight_train_sweeper() -> None:
    """周期回收过期终态训练作业（H-B6/R-08）：仅按 TTL/数量回收终态作业（``_prune_finished``），

    **绝不**触碰在跑 run、**绝不**调用全局 ``reconcile_dangling_weight_runs``（那会把同进程内正在跑的 run
    误判 failed）。cancel-safe：关停取消时干净退出、不外抛；其余异常仅 warning 不让协程死亡。
    """
    manager = get_weight_train_manager()
    try:
        while True:
            await asyncio.sleep(120)
            try:
                manager._prune_finished()
            except Exception as exc:  # noqa: BLE001 —— 单次清扫失败不得拖垮常驻协程
                logger.warning("weight-train sweeper prune skipped: %s", exc)
    except asyncio.CancelledError:    # R-08：关停取消时干净退出
        return


app = FastAPI(title="Aragon Trading Quant API", version="0.1.0", lifespan=lifespan)

# 单机本地工具：开发期放开 CORS（前端走 Next 代理，浏览器直连 WS 也需放开）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    # F2：仅"跨源直连"FastAPI（绕过 Next 同源代理，如本地调试 :18760）时才靠它放行自定义头；
    # 经 /api/py/* 同源代理的默认路径不依赖它（Next rewrite 默认透传上游响应头）。无害零成本，保留。
    expose_headers=["X-Aragon-Data-Source", "X-Aragon-Stale"],
)


@app.exception_handler(OKXError)
async def okx_error_handler(request: Request, exc: OKXError) -> JSONResponse:
    """OKX 不可达 -> 显式 502，严禁假数据 (C1)。"""
    return JSONResponse(
        status_code=502,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


# 统一把 HTTPException 的 dict detail 包成 {error:{...}}
from fastapi.exceptions import RequestValidationError  # noqa: E402
from starlette.exceptions import HTTPException as _StarletteHTTPException  # noqa: E402


def _validation_message(exc: RequestValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error.get("loc", []) if part != "body")
        msg = str(error.get("msg") or "Invalid value")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts) or "Invalid request parameters"


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "VALIDATION_ERROR", "message": _validation_message(exc)}},
    )


@app.exception_handler(_StarletteHTTPException)
async def http_exception_handler(request: Request, exc: _StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        body = {"error": detail}
    elif isinstance(detail, dict) and "error" in detail:
        body = detail
    else:
        body = {"error": {"code": str(exc.status_code), "message": str(detail)}}
    return JSONResponse(status_code=exc.status_code, content=body)


# 路由全部挂在 /api 下；ws 路由也在 /api 前缀（与前端 NEXT_PUBLIC_PY_WS_URL 一致 -> /api/ws/*）
app.include_router(market.router, prefix="/api")
app.include_router(strategies.router, prefix="/api")
app.include_router(runs.router, prefix="/api")
app.include_router(paper.router, prefix="/api")
app.include_router(ws_market.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(accounts.router, prefix="/api")
app.include_router(trading.router, prefix="/api")
app.include_router(factors.router, prefix="/api")   # 第 9 个路由（因子层 §3.1）
app.include_router(live_auto.router, prefix="/api")  # 第 10 个路由（实时自动交易，live-auto-trading §4）
app.include_router(app_settings.router, prefix="/api")  # 第 11 个路由（设置中心 · 自动交易设置 §4.1）
app.include_router(workspace.router, prefix="/api")  # 第 12 个路由（workspace 偏好持久化，watchlist-auto-trade-persistence §3）
app.include_router(weight_models.router, prefix="/api")  # 权重模型工作区（weight-model-workspace §五；含 WS /ws/weight-train/{jobId}）


@app.get("/")
async def root() -> dict:
    return {"name": "Aragon Trading Quant API", "docs": "/docs", "health": "/api/health"}
