"""路由：AI 自动交易的生命周期动作（ai-trade-pause-resume §五）。

pause / resume / end / resume-preview / quit-pause 五条。**刻意不放进 `api/ai_auto.py`**——
它已 780 行，加进去必破 CLAUDE.md 的 800 行红线。

守门极性（与既有 `/stop`、`/backstop/repair` 同理）：
- `pause` / `end` / `quit-pause` **不守 AI 总闸**——收敛风险的动作任何时候都该能执行；
- `resume` **守双闸**（`_require_ai_enabled`）——它会开新仓；
- 三个写端点都守整轴总闸 `ai_trade_pause_enabled`（关 ⇒ 403，一键回滚到本轮之前的行为）；
- `resume-preview` 只读，不守 AI 总闸（先让用户看清再决定）。

⚠️ 路由顺序：本模块的路径都带 `{group_id}` 前缀段，与 `api/ai_auto.py` 里
`/ai-trade/history/{group_id}` 之类的既有路径**不同层级**，不会互相吞（G-22 冻结这一条）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..ai_auto import get_ai_trade_manager
from ..ai_auto.manager import AiTradePreflightError
from ..ai_auto.resume import AiTradeResumeBlocked, assess_group, preview_wire
from ..config import Settings, get_settings
from ..schemas import AiTradeEndIn, AiTradePauseIn, AiTradeResumeIn

router = APIRouter(tags=["ai-trade"])


def _require_pause_enabled(settings: Settings) -> None:
    """整轴总闸：关 ⇒ 403（本轮全部写端点一键回滚，与启动对账的逐字回退同一个开关）。"""
    if not settings.ai_trade_pause_enabled:
        raise HTTPException(status_code=403, detail={
            "code": "AI_TRADE_PAUSE_DISABLED",
            "message": "本机已关闭 AI 自动交易的暂停 / 继续功能（AI_TRADE_PAUSE_ENABLED）"})


def _require_ai_enabled(settings: Settings) -> None:
    """`resume` 专用：它会开新仓，故与 `/ai-trade/start` 同守双闸。"""
    if not (settings.ai_trade_enabled and settings.live_trading_enabled):
        raise HTTPException(status_code=403, detail={
            "code": "AI_TRADE_DISABLED",
            "message": "AI 自动交易未启用（需 AI_TRADE_ENABLED 与 LIVE_TRADING_ENABLED 均开启）"})


def _http_error(exc: AiTradePreflightError) -> HTTPException:
    """服务层稳定语义码 → HTTP（`members` 明细须透传，前端 `describeStartError` 直接复用）。"""
    detail = {"code": exc.code, "message": exc.message}
    members = getattr(exc, "members", None)
    if members is not None:
        detail["members"] = members
    return HTTPException(exc.status, detail=detail)


@router.post("/ai-trade/{group_id}/pause")
async def ai_trade_pause(group_id: str, body: AiTradePauseIn | None = None,
                         settings: Settings = Depends(get_settings)) -> dict:
    """挂起分组：停一切新决策与开仓，**保留持仓**（也可选择先平仓再暂停）。

    不守 AI 总闸（收敛动作恒可用）。已是 `paused` ⇒ 409（幂等语义由服务层保证，但对外必须
    显式，绝不静默）。
    """
    _require_pause_enabled(settings)
    payload = body or AiTradePauseIn()
    hold = (settings.ai_trade_pause_hold_position_default if payload.holdPosition is None
            else payload.holdPosition)
    try:
        result = await get_ai_trade_manager().pause_group(
            group_id, reason=payload.reason, flatten=not hold)
    except AiTradePreflightError as exc:
        raise _http_error(exc) from exc
    if result is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "分组不存在"})
    if result.get("alreadyPaused"):
        raise HTTPException(409, detail={"code": "AI_TRADE_NOT_RUNNING",
                                         "message": "该分组已处于暂停状态"})
    return result


@router.get("/ai-trade/{group_id}/resume-preview")
async def ai_trade_resume_preview(group_id: str) -> dict:
    """续跑复核（只读）：离线期间发生了什么、契约有没有漂移。

    会发私有请求（持仓 / 挂单）。**不守 AI 总闸**——先让用户看清再决定；判定性文案全部由后端
    生成并随 wire 下发，前端只渲染（§3.10）。
    """
    try:
        assessment = await assess_group(get_ai_trade_manager(), group_id)
    except AiTradePreflightError as exc:
        raise _http_error(exc) from exc
    return preview_wire(assessment)


@router.post("/ai-trade/{group_id}/resume")
async def ai_trade_resume(group_id: str, body: AiTradeResumeIn | None = None,
                          settings: Settings = Depends(get_settings)) -> dict:
    """继续：用同一份契约、同一批 `run_id` 重新武装会话（不变式 I-3）。

    守双闸 + 整轴总闸。热续跑（软件没关）直接重新武装；冷续跑走 `ai_auto/resume.py` 的七步
    流水线，接管前必须通过离线差异复核（R-1：绝不静默接管）。

    回执语义在 ai-trade-lifecycle-truth 里变了（§5.3）：从「我把它置成 running 了」变成
    「**我核实过它现在是 running，而且这是它武装到什么程度的证据**」——`group_status()` 里的
    `state` 是派生真值，`lifecycle` 是让人能反驳它的那份证据。

    三个**新增**的 409（均由 `AiTradePreflightError` 派生并带 `.members`，`_http_error` 已透传）：
    `AI_TRADE_GROUP_ENDED` / `AI_TRADE_RESUME_PARTIALLY_DEAD` / `AI_TRADE_RESUME_NOT_ARMED`。
    前端 `describeStartError` 的 code 白名单必须**同时**认得这三个，否则 `.members` 里的逐标的
    明细一个字都不会渲染（[评审 R1-1]：本仓库「wire 里有事实、用户那里没有」的第 N 次复发）。

    notice-state-sync §4.7：回执多一个**加法键** `notice` —— 「继续」此前是全仓唯一一条**没有
    判定性回执**的生命周期迁移，于是界面上的表现是「暂停那句话凭空不见了」而不是「换了一句话」。
    本端点 `-> dict` 且**无** `response_model`，新键不会被 FastAPI 静默吞掉。

    **为什么不塞进 `/ai-trade/status`**：那里的 `group` 是一个长期存在的只读视图，往里塞一个
    只在一次动作里成立的 `notice` 会让它变成第二个真源；`notice` 的有效期是这一次迁移，不是
    「直到下一次轮询」。前端用一个**扩展类型** `AiTradeResumeResult extends AiTradeGroupRow`
    承接，`groupStateFromStatus(res)` 是超集入参的消费者，一行都不用改。
    """
    _require_ai_enabled(settings)
    _require_pause_enabled(settings)
    payload = body or AiTradeResumeIn()
    try:
        group = await get_ai_trade_manager().resume_group(group_id,
                                                          acknowledged=payload.acknowledge)
    except AiTradeResumeBlocked as exc:
        raise _http_error(exc) from exc
    except AiTradePreflightError as exc:
        raise _http_error(exc) from exc
    # **空 body 绝不并 notice**：`group_status()` 返回 `None` 时原样回 `{}`，否则会造出一份
    # 「只有 notice、没有任何分组字段」的回执体，而 `groupStateFromStatus(res)` 是它的消费者。
    manager = get_ai_trade_manager()
    status = manager.group_status()
    if not status:
        return {}
    return {**status, "notice": manager.build_resume_notice_for(group)}


@router.post("/ai-trade/{group_id}/end")
async def ai_trade_end(group_id: str, body: AiTradeEndIn | None = None,
                       settings: Settings = Depends(get_settings)) -> dict:
    """结束并归档一个已暂停的分组（可选顺带平仓）。不守 AI 总闸。

    [P1-12]：**带仓时 `flatten` 必填**，且「保留持仓」必须 `acknowledgeOrphanPosition=true`。
    回执必带 `orphanPositions`（此次结束后仍留在交易所上的持仓 + 各自的兜底覆盖情况），
    前端据它出一条**持久**提示（不是 toast）。
    """
    _require_pause_enabled(settings)
    payload = body or AiTradeEndIn()
    try:
        result = await get_ai_trade_manager().end_group(
            group_id, flatten=payload.flatten,
            acknowledged=payload.acknowledgeOrphanPosition)
    except AiTradePreflightError as exc:
        raise _http_error(exc) from exc
    if result is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "分组不存在"})
    return result


@router.post("/ai-trade/quit-pause")
async def ai_trade_quit_pause(settings: Settings = Depends(get_settings)) -> dict:
    """桌面端退出前的最后一次机会（§3.8）：按 `ai_trade_shutdown_action` 处置运行中分组。

    **纯优化，不承担正确性**——强杀 / 崩溃 / 断电由启动对账兜底（§3.7）。但它对**行为**不是
    中性的：D-4 证明 lifespan 那条平仓路径几乎从不执行，而本钩子是能跑通的 HTTP，故默认必须是
    `hold`（带仓暂停）。**不守 AI 总闸**（与 `/stop` 同理），但守整轴总闸。
    """
    _require_pause_enabled(settings)
    try:
        return await get_ai_trade_manager().shutdown()
    except AiTradePreflightError as exc:
        raise _http_error(exc) from exc
