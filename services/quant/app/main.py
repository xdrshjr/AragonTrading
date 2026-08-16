"""FastAPI 应用入口 (spec §8.3)。

挂载路由、CORS、启动/关闭钩子、OKXError -> 统一 5xx 错误体 (C1)。
启动：uvicorn app.main:app --port 18760
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .agent.manager import get_agent_manager
from .ai_auto import get_ai_trade_manager
from .ai_auto.backtest_manager import get_ai_backtest_manager
from .api import (
    accounts,
    agent,
    ai_auto,
    ai_trade_lifecycle,
    app_settings,
    discovery,
    discovery_assets,
    factors,
    live_auto,
    market,
    okx_link,
    paper,
    runs,
    strategies,
    trading,
    weight_batches,
    weight_models,
    workspace,
    ws_market,
)
from .db.session import dispose_engine, init_db
from .discovery.health_runtime import get_health_guard
from .discovery.manager import get_discovery_manager
from .factors.weight_batch_manager import get_weight_batch_manager
from .factors.weight_train_manager import get_weight_train_manager
from .live_auto.manager import get_live_auto_manager
from .okx.client_pool import get_client_pool
from .okx.clock import get_server_clock
from .okx.rest_client import OKXError, get_rest_client
from .paper.manager import get_paper_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("aragon")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("DB initialized; builtin strategies seeded.")
    await _backfill_companion_strategies()
    await _backfill_discovery_assets()         # 成果资产台账的一次性存量回填（D-29）
    await _reconcile_weight_train_runs()
    sweeper = asyncio.create_task(_weight_train_sweeper())   # H-B6：周期回收过期终态作业（cancel-safe）
    resumer = asyncio.create_task(_resume_discovery_runs())  # 挖掘任务自动续跑（静置延时后拉起）
    hydrator = asyncio.create_task(_hydrate_paused_ai_trade())  # AI 自动交易冷占位（不启动任何交易）
    get_health_guard().start()                 # 持续样本外体检守护（双闸每 tick 重读，D-24）
    link_sweeper = asyncio.create_task(_okx_link_sweeper())   # 私有连接池清扫 + 服务器时钟对齐
    yield
    link_sweeper.cancel()
    hydrator.cancel()
    resumer.cancel()
    sweeper.cancel()                                         # 关停先取消清扫协程（与 manager.shutdown 同段）
    await get_health_guard().shutdown()         # 体检守护先停（它只读行情，无终态可等）
    await get_agent_manager().shutdown()
    await get_discovery_manager().shutdown()   # 策略与因子挖掘（长跑循环，先停再收 DB / OKX）
    await get_paper_manager().shutdown()
    await get_ai_trade_manager().shutdown()   # AI 自动交易（紧随 live-auto 关停，含平仓）
    await get_ai_backtest_manager().shutdown()   # AI 回测作业（取消所有在跑 job，无真单）
    await get_live_auto_manager().shutdown()
    # 关停顺序（weight-multi-model-training §2.6）：先停批次泵、再停底层作业，避免泵在作业消失后空转。
    await get_weight_batch_manager().shutdown()
    await get_weight_train_manager().shutdown()
    await get_rest_client().aclose()
    await get_client_pool().aclose_all()   # 池化的私有 AsyncClient 统一收口（okx-link-resilience §2.4.1）
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


async def _backfill_discovery_assets() -> None:
    """把本功能上线前已 kept 的候选一次性补进成果资产台账（discovery-asset-ledger §2.11）。

    姿态完全镜像 ``_backfill_companion_strategies``：一次性 session + **整段 try/except，
    失败仅 warning 不阻断启动**（台账是观测面）。幂等靠 ``asset_key`` 唯一索引与
    ``register_kept`` 的同一条 upsert 路径，跑一百次结果相同；有界与错峰见 ``backfill_assets``。
    """
    try:
        from .config import get_settings
        from .db.session import get_sessionmaker
        from .discovery import assets as asset_service

        async with get_sessionmaker()() as session:
            stats = await asset_service.backfill_assets(session, get_settings())
        if stats.created or stats.merged:
            logger.info("backfilled discovery assets: %d created, %d merged, %d skipped",
                        stats.created, stats.merged, stats.skipped)
    except Exception as exc:  # noqa: BLE001 —— 启动回填非关键，绝不因它拖垮服务启动
        logger.warning("discovery asset backfill skipped: %s", exc)


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


async def _resume_discovery_runs() -> None:
    """自动续跑上次崩溃残留的挖掘任务（strategy-factor-discovery §2.1 纪律 4 / §6）。

    ``init_db`` 里的对账已把 ``running`` 孤儿一律置 ``paused`` 并给出 ``action``；这里只负责在
    ``discovery_resume_delay_s`` 静置延时（等 OKX / DB 就绪）后把 ``resumable`` 的那些拉起。
    **总闸关闭时对账给出的全是 ``paused``，本协程自然一个也不拉起**——这正是评审 P1-8 要的
    「关了就是真的关了」。整段 try/except，失败仅 warning 不阻断启动；cancel-safe。
    """
    try:
        from .config import get_settings
        from .db.session import get_sessionmaker
        from .discovery import service as discovery_service
        from .discovery.manager import reconcile_orphan_runs

        settings = get_settings()
        pending = [item for item in await reconcile_orphan_runs()
                   if item.get("action") == "resumable"]
        if not pending:
            return
        await asyncio.sleep(float(settings.discovery_resume_delay_s))
        for item in pending:
            try:
                async with get_sessionmaker()() as session:
                    await discovery_service.resume_run(session, get_rest_client(),
                                                       int(item["runId"]))
                logger.info("resumed discovery run %s from checkpoint", item["runId"])
            except Exception as exc:  # noqa: BLE001 —— 单条拉起失败（如并发已满）不得吃掉其余
                logger.warning("discovery run %s auto-resume skipped: %s", item["runId"], exc)
    except asyncio.CancelledError:      # 关停取消时干净退出
        return
    except Exception as exc:  # noqa: BLE001 —— 自动续跑非关键，绝不因它拖垮服务启动
        logger.warning("discovery auto-resume skipped: %s", exc)


async def _hydrate_paused_ai_trade() -> None:
    """冷启装上「可续跑的 AI 自动交易分组」轻量占位（ai-trade-pause-resume §3.7）。**不启动任何交易。**

    姿态镜像 `_backfill_companion_strategies`（一次性 session + 整段 try/except，不阻断启动），
    但多两条 —— 本协程装载的不是观测面，而是**互斥与容量闸的真值**（[P0-3]）：
    1. **有界重试**：3 次、退避 1s（DB 在启动瞬间被 `init_db` 占着是最常见的失败原因）；
    2. **失败即置盲区标志**：`manager.note_hydration_failed()` 让 `status()` 回一个非空占位
       （`live_auto` 零改动地保持互斥），并让 AI 侧容量闸的那次 DB 读在成功时顺手把它清掉。

    换句话说：**仍然不阻断启动（惯例不破），但「我不知道」被显式表示出来，而不是被表示成
    「没有」**。另含 §八风险 11 的自愈：run 已 `stopped` 而状态表仍 `paused` ⇒ 置 `ended`。
    """
    from .ai_auto.manager import paused_ref_from_state_row
    from .config import get_settings
    from .db import ai_trade_state_repositories as state_repo
    from .db import repositories as repo
    from .db.session import get_sessionmaker

    if not get_settings().ai_trade_pause_enabled:
        return
    for attempt in range(3):
        try:
            async with get_sessionmaker()() as session:
                row = await state_repo.load_paused_group_brief(session)
                if row is None:
                    return
                alive = await repo.list_paused_ai_trade_group_ids(session)
                if row["groupId"] not in alive:
                    # 状态表落后于 run（W5 那次写失败过）：以**状态表为准**是本特性的原则，但当
                    # run 已终态时它显然过期了 —— 置 ended 并 warning，绝不留一张指向已平仓会话
                    # 的续跑卡（§八风险 11）。
                    await state_repo.patch_group_state(session, row["groupId"],
                                                       {"status": "ended"})
                    logger.warning("ai-trade paused contract %s dropped: runs already finalized",
                                   row["groupId"])
                    return
            get_ai_trade_manager().adopt_paused_ref(paused_ref_from_state_row(row))
            logger.info("ai-trade resumable group hydrated: %s", row["groupId"])
            return
        except asyncio.CancelledError:      # 关停取消时干净退出
            return
        except Exception as exc:  # noqa: BLE001 —— 有界重试；三次都失败才置盲区标志
            logger.warning("ai-trade paused-group hydration attempt %d failed: %s",
                           attempt + 1, exc)
            await asyncio.sleep(1.0)
    get_ai_trade_manager().note_hydration_failed()


async def _weight_train_sweeper() -> None:
    """周期回收过期终态训练作业（H-B6/R-08）：仅按 TTL/数量回收终态作业（``_prune_finished``），

    **绝不**触碰在跑 run、**绝不**调用全局 ``reconcile_dangling_weight_runs``（那会把同进程内正在跑的 run
    误判 failed）。cancel-safe：关停取消时干净退出、不外抛；其余异常仅 warning 不让协程死亡。
    """
    manager = get_weight_train_manager()
    batch_manager = get_weight_batch_manager()
    try:
        while True:
            await asyncio.sleep(120)
            try:
                manager._prune_finished()
                batch_manager._prune_finished()   # 批次运行时同周期按 TTL/数量回收（weight-multi-model-training §2.6）
            except Exception as exc:  # noqa: BLE001 —— 单次清扫失败不得拖垮常驻协程
                logger.warning("weight-train sweeper prune skipped: %s", exc)
    except asyncio.CancelledError:    # R-08：关停取消时干净退出
        return


async def _okx_link_sweeper() -> None:
    """OKX 链路后台维护（okx-link-resilience §2.4.1 / §2.4.2）：连接池清扫 + 服务器时钟对齐。

    姿态完全镜像既有 ``_weight_train_sweeper``：60s 一跳、cancel-safe、单次失败只 warning 不让
    协程死亡。启动即先同步一次时钟——本机时钟漂移（Windows 笔记本休眠唤醒后尤甚）会让**全部**
    私有请求在同一瞬间撞 ``50102``，而此前它之后没有任何自愈动作。
    """
    from .config import get_settings

    clock = get_server_clock()
    pool = get_client_pool()
    try:
        while True:
            # 两件维护**互不连坐**：时钟同步走的是公共读，公共链路一断它必抛，而 `pool.sweep()`
            # 排在它后面就跟着一起被跳过——生产上公共读死了三天，私有连接池的清扫也整整三天
            # 一次都没跑过（okx-public-rest-circuit-open-never-recovers §3.4）。
            try:
                settings = get_settings()
                if clock.is_stale(settings.okx_link_clock_sync_interval_s):
                    await clock.sync(get_rest_client().get_server_time_ms)
            except Exception as exc:  # noqa: BLE001 —— 单次维护失败不得拖垮常驻协程
                logger.warning("okx link sweeper tick skipped: %s", exc)
            try:
                closed = await pool.sweep(time.monotonic())
                if closed:
                    logger.info("okx client pool swept %d idle clients", closed)
            except Exception as exc:  # noqa: BLE001 —— 同上，且不得反过来连坐时钟同步
                logger.warning("okx client pool sweep skipped: %s", exc)
            await asyncio.sleep(60)
    except asyncio.CancelledError:    # 关停取消时干净退出
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
app.include_router(ai_auto.router, prefix="/api")   # AI 自动交易（ai-auto-trading §4；含 WS /ws/ai-trade/{sid}）
app.include_router(ai_trade_lifecycle.router, prefix="/api")  # AI 自动交易生命周期（暂停/继续/结束，ai-trade-pause-resume §五）
app.include_router(app_settings.router, prefix="/api")  # 第 11 个路由（设置中心 · 自动交易设置 §4.1）
app.include_router(workspace.router, prefix="/api")  # 第 12 个路由（workspace 偏好持久化，watchlist-auto-trade-persistence §3）
app.include_router(weight_models.router, prefix="/api")  # 权重模型工作区（weight-model-workspace §五；含 WS /ws/weight-train/{jobId}）
app.include_router(weight_batches.router, prefix="/api")  # 多模型训练竞技场（weight-multi-model-training §四；含 WS /ws/weight-batch/{batchId}）
app.include_router(discovery.router, prefix="/api")  # 第 16 个路由（策略与因子挖掘 §四；含 WS /ws/discovery/{runId}）
app.include_router(okx_link.router, prefix="/api")   # OKX 链路健康只读端点（okx-link-resilience §四）
app.include_router(discovery_assets.router, prefix="/api")  # 第 17 个路由（成果资产台账与持续体检 §四，无 WS）


@app.get("/")
async def root() -> dict:
    return {"name": "Aragon Trading Quant API", "docs": "/docs", "health": "/api/health"}
