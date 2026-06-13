"""FastAPI 应用入口 (spec §8.3)。

挂载路由、CORS、启动/关闭钩子、OKXError -> 统一 5xx 错误体 (C1)。
启动：uvicorn app.main:app --port 18760
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import market, paper, runs, strategies, ws_market
from .db.session import dispose_engine, init_db
from .okx.rest_client import OKXError, get_rest_client
from .paper.manager import get_paper_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("aragon")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("DB initialized; builtin strategies seeded.")
    yield
    await get_paper_manager().shutdown()
    await get_rest_client().aclose()
    await dispose_engine()


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
from fastapi.exceptions import HTTPException as _HTTPException  # noqa: E402
from starlette.exceptions import HTTPException as _StarletteHTTPException  # noqa: E402


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


@app.get("/")
async def root() -> dict:
    return {"name": "Aragon Trading Quant API", "docs": "/docs", "health": "/api/health"}
