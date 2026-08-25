"""路由：交易经验库（experience-context-graph §五 + experience-compounding §5.1）。

十四个端点，全部走 `/api` 前缀并经既有 `OriginGuardMiddleware`（ASGI 中间件，新路由自动受保护）。
错误体统一 `{error:{code,message}}`（`main.py` 全局处理器包装）。

三条贯穿本模块的纪律：

- **阈值一律经 `resolve_experience_settings` 取**（★ E-3 / 不变式 I-10）。直接读 `get_settings()`
  会让设置中心那个「阈值设置」面板变成一个用户改得动、系统永远不读、而且不报错的假面板。
  受此约束的三个端点：`/rebuild`、`/preview`、`GET|PUT /settings/experience`（后者在
  `api/app_settings.py`）。
- **CPU 密集段走 `run_in_threadpool`**（沿用 `run_backtest` / `prior_service` 的既有范式），
  绝不在事件循环上跑全库重建。
- **导出必须流式**：跑半年的库是几万条事件，一次性拼进内存再序列化会在桌面端把整个进程顶起来。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_auto import get_ai_trade_manager
from ..ai_auto.experience import service as experience_service
from ..ai_auto.experience import taxonomy as tax
from ..ai_auto.experience.service import ImportTooLargeError
from ..config import Settings, get_settings
from ..db import repositories as repo
from ..db.session import get_session
from ..schemas import (
    ExperienceImportIn,
    ExperienceNodeIn,
    ExperienceNodePatchIn,
    ExperiencePreviewIn,
    ExperiencePruneIn,
    ExperienceRebuildIn,
)

router = APIRouter(tags=["experience"])

# `stat` 节点删不掉：下一次物化会按 `key` 把它原样复活，删除因此是一个**假动作**。
# 回执里那句可执行提示（「改用静音」）必须与 `PATCH {muted:true}` 真的可用这件事对齐——
# 产品告诉用户的做法，产品自己必须兑现（★ E-9 让自动静音可解除、用户静音不可被覆盖）。
_STAT_UNDELETABLE = "EXPERIENCE_STAT_NODE_UNDELETABLE"


def _filters(inst_id: str | None, bar: str | None, axis: str | None, tier: str | None,
             source: str | None, q: str | None) -> dict:
    """把查询串收敛成 `service._match` 认识的形状（图谱与列表**共用**，A-22 的支点）。"""
    return {"instId": inst_id, "bar": bar, "axis": axis, "tier": tier, "source": source, "q": q}


@router.get("/experience/overview")
async def experience_overview(session: AsyncSession = Depends(get_session),
                              settings: Settings = Depends(get_settings)) -> dict:
    """概览条：档位分布 + 事件样本 + 覆盖标的 + 三个诚实性计数 + 物化健康（九个键）。

    `materializeHealth` 是 F-2 那堵规模墙的露出点，`harvestGapTotal` 是 R-15 那段幸存者偏差的
    露出点。两者在 §7.1 各有明确渲染落点——后端算了却没有呈现位置的观测量等于死码（I-8）。

    ★ 本轮新增 `harvest`（采集完整性明细）与 `utility`（效用闭环），阈值经
    `resolve_experience_settings` 取——`utility` 的总闸是可写配置，直接读 `get_settings()`
    会让面板上关掉的东西在这里照常下发（★ E-3 / I-10）。
    """
    derived = await experience_service.resolve_experience_settings(session, settings)
    return await experience_service.overview(session, derived)


@router.get("/experience/graph")
async def experience_graph(instId: str | None = None, bar: str | None = None,
                           axis: str | None = None, tier: str | None = None,
                           source: str | None = None, q: str | None = None,
                           limit: int = Query(default=120, ge=1, le=2000),
                           session: AsyncSession = Depends(get_session)) -> dict:
    """图谱取数（按 `weight` 降序截断，`truncated` **如实**下发）。

    静默截断读起来像「全都在这儿了」，那是本仓库反复付过学费的一条：UI 必须在图上明示
    「已按权重展示前 N / 共 M 个节点」。
    """
    filters = {**_filters(instId, bar, axis, tier, source, q), "limit": limit}
    return await experience_service.graph_view(session, filters)


@router.get("/experience/lessons")
async def experience_lessons(instId: str | None = None, bar: str | None = None,
                             axis: str | None = None, tier: str | None = None,
                             source: str | None = None, q: str | None = None,
                             offset: int = Query(default=0, ge=0),
                             limit: int = Query(default=50, ge=1, le=500),
                             session: AsyncSession = Depends(get_session),
                             settings: Settings = Depends(get_settings)) -> dict:
    """列表视图——图谱的**无障碍等价形态**（只有图的界面对读屏用户不可用）。

    与图谱**同数据源同筛选判据**，故同一筛选下的条数在结构上不可能不一致（A-22）。
    """
    filters = {**_filters(instId, bar, axis, tier, source, q), "offset": offset, "limit": limit}
    derived = await experience_service.resolve_experience_settings(session, settings)
    return await experience_service.lessons_view(session, derived, filters)


@router.get("/experience/nodes/{node_id}")
async def experience_node_detail(node_id: int,
                                 evidenceLimit: int = Query(default=20, ge=1, le=200),
                                 session: AsyncSession = Depends(get_session),
                                 settings: Settings = Depends(get_settings)) -> dict:
    """详情抽屉：统计 / 语境签名 / 版本链 / 冲突 / 最近 N 笔溯源证据 + **注入后表现**。"""
    derived = await experience_service.resolve_experience_settings(session, settings)
    detail = await experience_service.node_detail(session, derived, node_id,
                                                  evidence_limit=evidenceLimit)
    if detail is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "经验节点不存在"})
    return detail


@router.post("/experience/nodes", status_code=201)
async def experience_node_create(body: ExperienceNodeIn,
                                 session: AsyncSession = Depends(get_session)) -> dict:
    """手工新增一条经验（`source="manual"`，`tier` 恒 `candidate`）。

    **一进门绝不能是铁律**（不变式 I-4）：手写经验要进 L0，要么被 ≥`min_samples` 条确定性事件
    佐证，要么由用户**显式置顶**——后者是一次独立的人类动作，而不是写下来的副作用。
    """
    clean = experience_service.sanitize_lesson(
        {"statement": body.statement, "detail": body.detail, "context": body.context},
        source="manual")
    if clean is None:
        raise HTTPException(422, detail={
            "code": "EXPERIENCE_CONTEXT_INVALID",
            "message": f"语境轴或取值不在闭合词表内（可用轴：{'、'.join(tax.AXES)}）"})
    clean["stats"] = {"n": 0, "manual": True, "polarity": body.polarity}
    return {"node": await repo.create_experience_node(session, clean)}


@router.patch("/experience/nodes/{node_id}")
async def experience_node_patch(node_id: int, body: ExperienceNodePatchIn,
                                session: AsyncSession = Depends(get_session)) -> dict:
    """编辑一条经验（陈述 / 长文 / 置顶 / 静音 / 档位）。

    `tier` 只在 `candidate`/`normal`/`iron` 之间调整，且**升 `iron` 只有一条路径——置顶**（I-4）：
    直接把一条 `candidate` 改成 `iron` 会绕过 I-4 的全部门槛，故未置顶时该请求被拒。
    """
    row = await repo.get_experience_node(session, node_id)
    if row is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "经验节点不存在"})
    pinned = bool(row.get("pinned")) if body.pinned is None else bool(body.pinned)
    if body.tier == "iron" and not pinned:
        raise HTTPException(422, detail={
            "code": "EXPERIENCE_IRON_REQUIRES_PIN",
            "message": "升到「铁律」只有一条路径：先置顶这条经验（叙事与手写经验不得自动进纲要）"})
    patch = {"label": body.statement, "detail": body.detail, "pinned": body.pinned,
             "muted": body.muted, "tier": body.tier}
    node = await repo.patch_experience_node(
        session, node_id, {k: v for k, v in patch.items() if v is not None})
    return {"node": node}


@router.delete("/experience/nodes/{node_id}", status_code=204)
async def experience_node_delete(node_id: int,
                                 session: AsyncSession = Depends(get_session)) -> Response:
    """删除一条经验；`source == "stat"` ⇒ 409 并给出**可执行**的替代动作。

    统计经验删了会被下一次物化按 `key` 原样复活——那是一个假动作，比拒绝更坏。
    """
    row = await repo.get_experience_node(session, node_id)
    if row is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "经验节点不存在"})
    if row.get("source") == "stat":
        raise HTTPException(409, detail={
            "code": _STAT_UNDELETABLE,
            "message": "统计经验不能删除（下一次重建会按同一 key 复活它）。请改用「静音」——"
                       "静音是用户意志，物化永远不会覆盖它。"})
    await repo.delete_experience_node(session, node_id)
    return Response(status_code=204)


@router.post("/experience/rebuild")
async def experience_rebuild(body: ExperienceRebuildIn,
                             session: AsyncSession = Depends(get_session),
                             settings: Settings = Depends(get_settings)) -> dict:
    """从事件全量重建统计子图（手动触发，**无去抖、无规模闸**——这是用户明确要求的动作）。

    阈值经 `resolve_experience_settings` 取（★ E-3）：不这样做，用户刚在上面那个面板里把
    「铁律样本门槛」从 20 改成 5、保存、再点「重建图谱」，得到的还是旧阈值的结果。

    ★ 本轮 `scope` 真的开始有取值了：`"full"` = **强制**全量并 `epoch+1`（下拉菜单里那一项），
    `"stat"`（默认）= 由 W-B 的五条判据自选增量还是全量。两者都是用户明确要求的动作，
    都不受去抖与规模闸约束。

    走 `rebuild_manual` 而不是裸 `rebuild`：它与后台物化共用同一处健康写回。少了那一步，
    §7.1 概览条的「上次重建」永远停在上一次**自动**物化的时刻，而 `torn` / `overflow` 两条
    警告里写着的「建议手动重建」在用户照做之后仍不消失。

    **客户端必须落长跑档**（★ E-11）：`lib/httpTimeout.ts` 的 `LONG_EXACT_PREFIXES` 已登记
    `/experience/rebuild`——F-2 自己就论证了全库重建在大库上超 20 秒是常态，落 `FAST_MS`
    会让这个按钮**必然**假超时。
    """
    derived = await experience_service.resolve_experience_settings(session, settings)
    try:
        # **等锁必须有界**，即便这是一个用户明确要求的动作：裸 `async with` 在持锁者异常消失后
        # 会把这个请求永久挂住，而挂起的 HTTP 请求既不返回也不报错。给它比后台任务宽裕得多的
        # 死线（×3），等不到就如实回 409 让用户稍后再试——「正在重建」是一个可执行的答案。
        return await experience_service.rebuild_manual(
            session, derived, scope=body.scope,
            lock_timeout=float(derived.ai_trade_experience_materialize_timeout_s) * 3)
    except experience_service.MaterializeBusyError as exc:
        raise HTTPException(409, detail={
            "code": "EXPERIENCE_REBUILD_BUSY",
            "message": "另一次重建正在进行中，请稍后再试（后台的自动重建也走同一把锁）。"}) from exc


@router.post("/experience/preview")
async def experience_preview(body: ExperiencePreviewIn,
                             session: AsyncSession = Depends(get_session),
                             settings: Settings = Depends(get_settings)) -> dict:
    """检索预演：**逐字**渲染下一轮 Agent 会看到的 L0 / L1（§7.1 最有说服力的一屏）。

    它把「Agent 到底记住了什么」从黑箱变成可核对的事实——但**它必须说实话**：预演读的是当前
    DB 里的图，运行中的会话用的是启动那一刻的内存快照，两者会分叉。故回执带 `graphAsOfTs` 与
    `liveSessions[]`（由 manager **现查内存会话**得到，不查 DB），UI 在分叉时明说。

    阈值同样经 `resolve_experience_settings` 取——否则预演用的阈值与真实会话不同，这一屏又变成
    假话（★ E-3）。
    """
    derived = await experience_service.resolve_experience_settings(session, settings)
    axes = {
        tax.AXIS_SYMBOL: body.instId, tax.AXIS_BAR: body.bar, tax.AXIS_REGIME: body.regime,
        tax.AXIS_VOLATILITY: body.volatility, tax.AXIS_EVENT: body.event,
        tax.AXIS_SIDE: body.side, tax.AXIS_WAKE: body.wake, tax.AXIS_EXIT: body.exit,
        tax.AXIS_HORIZON: body.horizon, tax.AXIS_CONFIDENCE: body.confidence,
        tax.AXIS_COST_REGIME: body.costRegime,
    }
    return await experience_service.preview(
        session, derived, axes,
        live_sessions=get_ai_trade_manager().experience_live_sessions())


@router.get("/experience/utility")
async def experience_utility(instId: str | None = None, bar: str | None = None,
                             minInjected: int = Query(default=0, ge=0),
                             limit: int = Query(default=200, ge=1, le=500),
                             session: AsyncSession = Depends(get_session),
                             settings: Settings = Depends(get_settings)) -> dict:
    """效用面板：「这条经验被喂进去之后，那些单后来怎么样」。

    ★ **J-5 · 这是共现，不是因果。** 我们并不知道模型是否真的采纳了其中任何一条，故全部 wire 键
    与文案一律用「注入后表现」「与事实一致率」，绝不使用「贡献」「带来」（G-13 源码扫描钉死）。

    筛选判据复用 `service._match`——与列表 / 图谱**同一个**，故同一筛选下三处的条数不可能不一致。
    """
    derived = await experience_service.resolve_experience_settings(session, settings)
    return await experience_service.utility_view(session, derived, {
        "instId": instId, "bar": bar, "minInjected": minInjected, "limit": limit})


@router.get("/experience/health")
async def experience_health(session: AsyncSession = Depends(get_session),
                            settings: Settings = Depends(get_settings)) -> dict:
    """物化健康 + epoch 现状 + 已自动静音条数（**轮询专用**，故从 `overview` 里拆出来）。

    `overview` 会扫整张节点表。把一个每几秒轮询一次的接口挂在一次全表扫上，是那种「先能跑、
    后来变慢、再也查不出为什么」的典型来源。启动弹窗那行「已自动静音 N 条」也取自这里——
    **零新增请求**。
    """
    derived = await experience_service.resolve_experience_settings(session, settings)
    return await experience_service.health_view(session, derived)


@router.post("/experience/prune")
async def experience_prune(body: ExperiencePruneIn,
                           session: AsyncSession = Depends(get_session)) -> dict:
    """清理**沉降**经验（`source="stat"` 且已静音且非用户静音且未置顶）。

    **绝不静默批删**：`dryRun=true` 必须先跑一次并把条数显示给用户，二次确认之后才真删
    （A-34 断言两次的条数一致）。置顶与用户静音一律跳过并如实回报条数——用户按下过的每一个
    动作都必须在这条路径上活着。

    客户端**必须落写路径超时档**（`lib/httpTimeout.ts` 的 `WRITE_PREFIXES`）：它是删除操作，
    超时要说「结果未知，请刷新核对」而不是「失败」。
    """
    return await experience_service.prune_stale(session, dry_run=bool(body.dryRun))


@router.get("/experience/export")
async def experience_export(instId: str | None = None, since: int | None = None,
                            until: int | None = None,
                            session: AsyncSession = Depends(get_session),
                            settings: Settings = Depends(get_settings)) -> StreamingResponse:
    """导出为 **NDJSON 流**（首行 `counts` 信封，其后每行一个 node / edge / event）。

    首行的 `counts` 让接收方能校验完整性——一条被网络截断的 NDJSON 与一条完整的在语法上没有
    任何区别，这是导出格式必须自带体检项的理由。
    """
    query = repo.ExperienceEventQuery(inst_id=instId, since_ts=since, until_ts=until)
    stream = experience_service.export_stream(
        session, query, page=int(settings.ai_trade_experience_export_page_size))
    return StreamingResponse(stream, media_type="application/x-ndjson", headers={
        "Content-Disposition": 'attachment; filename="aragon-experience.ndjson"'})


@router.post("/experience/import")
async def experience_import(body: ExperienceImportIn,
                            session: AsyncSession = Depends(get_session),
                            settings: Settings = Depends(get_settings)) -> dict:
    """导入 JSON（**不可信输入**）。只导入经验节点，边与事件一律计入 `rejected`。

    边是物化派生物（导进来就是别人的库的派生物，与本地事件不一致），事件带着别人的成本口径
    （I-7 一旦混用，整张表偏移一个往返成本）。这是一条有意的收窄，回执如实告知被拒条数。

    **★ E-10**：条数越界 ⇒ `422 EXPERIENCE_IMPORT_TOO_LARGE`，**一条都不写、绝不静默截断**；
    `mode="replace"` 的删与写在同一个事务里，中途失败整体回滚（绝不留下一个「删了一半」的库）。
    """
    try:
        return await experience_service.import_graph(
            session, body.model_dump(), mode=body.mode,
            max_nodes=int(settings.ai_trade_experience_import_max_nodes))
    except ImportTooLargeError as exc:
        raise HTTPException(422, detail={
            "code": "EXPERIENCE_IMPORT_TOO_LARGE",
            "message": f"导入的经验条数 {exc.count} 超过上限 {exc.limit}，本次一条都未写入。"
                       "请拆分文件后分批导入。"}) from exc
