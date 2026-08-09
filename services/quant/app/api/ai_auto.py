"""路由：AI 自动交易 start/stop/status + ws /ws/ai-trade/{sid}（ai-auto-trading §4）。

``start`` 受 ``AI_TRADE_ENABLED`` **与** ``LIVE_TRADING_ENABLED`` 双闸守门（自动路径直接调服务层，
须在此显式同时校验，任一 false → 403 ``AI_TRADE_DISABLED``，先于一切凭证读取）。``stop`` 刻意不守门：
即便运维中途关闭总闸，也须保证既有会话随时可停止并平掉自管持仓（安全优先）。``GET /status`` 只读不守门。
错误体统一 ``{error:{code,message}}``（经 main 全局 HTTPException 处理器包装）。
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_auto import get_ai_trade_manager
from ..ai_auto.backtest_manager import (
    AiBacktestError,
    AiBacktestStartRequest,
    get_ai_backtest_manager,
)
from ..ai_auto.backstop_repair import AiTradeRepairBlocked
from ..ai_auto.backstop_repair import fail_message as repair_fail_message
from ..ai_auto.base_rules import MaBandRule, resolve_ma_band_rule
from ..ai_auto.cost_runtime import CostRuntime
from ..ai_auto.hold_horizon import HorizonRule, profiles_wire, resolve_horizon_rule
from ..ai_auto.manager import (
    AiTradeGroupMemberInput,
    AiTradeGroupPreflightError,
    AiTradeGroupStartRequest,
    AiTradePreflightError,
    AiTradeSessionLimitError,
)
from ..ai_auto.prior_service import prior_snapshot_out, rebuild_prior_snapshot
from ..ai_auto.profit_guard import ProfitGuardRule, resolve_profit_guard_rule
from ..ai_auto.signal_prior import resolve_prior_params, strategy_fingerprint
from ..config import Settings, get_settings
from ..db import ai_prior_repositories as prior_repo
from ..db import repositories as repo
from ..db.session import get_session
from ..okx.private_client import OKXPrivateError
from ..okx.types import BAR_SECONDS
from ..paper.strategy_factory import build_paper_strategy_spec
from ..schemas import (
    AiBacktestStartIn,
    AiTradeBaseRulesIn,
    AiTradeGroupStartIn,
    AiTradeHistoryDetailOut,
    AiTradeHistoryListOut,
    AiTradeHorizonIn,
    AiTradeHorizonProfilesOut,
    AiTradePriorRebuildIn,
    AiTradePriorSnapshotOut,
    AiTradeProfitGuardIn,
    trade_out,
)

router = APIRouter(tags=["ai-trade"])

_WS_HEARTBEAT_SECONDS = 20.0   # 静默此秒数后下发 ping（半开探测 + 满队列丢终态兜底，镜像 weight-train）
_BACKTEST_TERMINAL_TYPES = ("done", "cancelled", "error")


def _require_ai_enabled(settings: Settings) -> None:
    """写类守门：AI 自动化总闸与底层写链路总闸须同时为 True，否则 403（先于凭证读取）。"""
    if not (settings.ai_trade_enabled and settings.live_trading_enabled):
        raise HTTPException(status_code=403, detail={
            "code": "AI_TRADE_DISABLED",
            "message": "AI 自动交易未启用（需 AI_TRADE_ENABLED 与 LIVE_TRADING_ENABLED 均开启）"})


def _resolve_position_step(raw: float | None, settings: Settings) -> float:
    """仓位操作档位步长解析（[R-2] **仅**缺省回填）：`None → settings 默认`，否则原样返回。

    不复夹 `[0,1]`——边界已由 schema `Field(ge=0, le=1)` 在入口拒绝（越界 422，与既有 `leverage:
    Field(gt=0, le=125)` 只在 schema 兜边界、API 不复夹的惯例一致）。`0` 为显式连续退出口，被接受。
    """
    return settings.ai_trade_default_position_step_pct if raw is None else raw


def _resolve_wake_interval_s(minutes: float | None, settings: Settings) -> float:
    """Agent 墙钟定时唤醒周期解析（分钟 → 秒并**夹紧**，与 `_resolve_position_step` 同风格但夹紧而非仅回填）。

    `None → 默认（ai_trade_wake_interval_default_min）`；`<=0 → 0`（关闭定时唤醒，退出口）；否则夹到
    `[ai_trade_wake_interval_floor_s, ai_trade_wake_interval_ceil_min*60]` 秒（越界不拒绝，向 UX 友好收敛；
    前端 validateWakeInterval 另做即时报错，二者区间一致、职责互补，RN-9）。
    """
    if minutes is None:
        minutes = settings.ai_trade_wake_interval_default_min
    if minutes <= 0:
        return 0.0
    lo = settings.ai_trade_wake_interval_floor_s
    hi = settings.ai_trade_wake_interval_ceil_min * 60.0
    return min(max(minutes * 60.0, lo), hi)


def _resolve_base_rules(raw: AiTradeBaseRulesIn | None,
                        settings: Settings) -> MaBandRule | None:
    """【基础策略】默认开启语义的**唯一解析点**（ai-trade-base-strategy-ma-band §2.10/P1-5）。

    字段缺省 ⇒ 按 settings 装配开启态（老客户端 / 桌面旧包不发该字段也默认开启，符合需求「默认开启」）；
    显式 `enabled=false` ⇒ `None`（该会话不装配）；运维总闸关 ⇒ 恒 `None`。

    **刻意不在 dataclass 层给开启默认值**：`AiTradeGroupStartRequest.base_rule` / `AiBacktestSpec`
    的字段默认仍是 `None`，故既有直接构造它们的单测行为逐字不变。实盘与回测共用本函数，保证「用回测
    调出的参数在实盘不失真」（P1-4）。
    """
    raw_rule = raw.maBandEntry if raw is not None else None
    return resolve_ma_band_rule(
        raw_rule.model_dump() if raw_rule is not None else None, settings)


def _resolve_profit_guard(raw: AiTradeProfitGuardIn | None,
                          settings: Settings) -> ProfitGuardRule | None:
    """利润保护层「默认开启」语义的**唯一解析点**（ai-trade-profit-protection §2.8）。

    字段缺省 ⇒ 按 settings 装配开启态（老客户端 / 桌面旧包不发该字段也受保护）；显式 `enabled=false`
    ⇒ `None`（该会话不装配）；运维总闸关 ⇒ 恒 `None`。

    **实盘与回测两处启动都走本函数**（ai-trade-profit-protection-adaptive §7.2a I-3，与
    `_resolve_base_rules` 重新对齐）：第 1 轮把回测列为非目标，是因为当时只想得到「用收盘价近似」这一
    种做法（会低估触发频次，属假数据）；第 2 轮 F3 改用 **bar 内 OHLC 极值 + 保守成交序**（§2.3.1，与
    `simulator.apply_risk` 处理止损止盈同一范式），故该限制解除。回测侧另有运维总闸
    `ai_trade_profit_guard_backtest_enabled`（false ⇒ 恒不装配，一键回到第 1 轮的回测行为），
    结果 notes 与 UI 必须同时显示 §2.3.5 的保守近似免责。
    """
    return resolve_profit_guard_rule(raw.model_dump() if raw is not None else None, settings)


def _resolve_backtest_profit_guard(raw: AiTradeProfitGuardIn | None,
                                   settings: Settings) -> ProfitGuardRule | None:
    """回测侧解析：在实盘那条语义之上再叠一层运维总闸（§2.3.4 的一键回退）。"""
    if not settings.ai_trade_profit_guard_backtest_enabled:
        return None
    return _resolve_profit_guard(raw, settings)


def _resolve_horizon(raw: AiTradeHorizonIn | None, settings: Settings, *,
                     bar: str) -> HorizonRule | None:
    """交易周期模式「默认开启」语义的**唯一解析点**（ai-trade-holding-horizon-modes §4.2 / D-2）。

    四层语义：运维总闸关 ⇒ 恒 `None`；字段缺省 ⇒ 按 settings 装配默认模式（老客户端 / 桌面旧包不发
    该字段也默认开启）；显式 `enabled=false` ⇒ `None`（该会话不装配）；逐项覆盖 ⇒ 覆盖预设。

    `bar` 是**用户实际选择的执行周期**：`expectedHoldBars` 与 `confirmWindowSeconds` 两个派生量按它
    现算，绝不按预设自带的建议周期算——否则用户把 `intraday_swing` 的周期从 1H 改成 5m 之后，资金费
    期数与 G2 的墙钟上界仍按 1H 的口径走，是一个不会报错的静默错配。

    **实盘与回测共用本函数**（`base_rules` P1-4 / `profit_guard` I-3 的成文教训）：两侧默认不一致，
    用户拿回测调出来的参数在实盘直接失真。
    """
    return resolve_horizon_rule(raw.model_dump() if raw is not None else None,
                                settings, bar=bar)


def _resolve_backtest_horizon(raw: AiTradeHorizonIn | None, settings: Settings, *,
                              bar: str) -> HorizonRule | None:
    """回测侧解析：在实盘那条语义之上再叠一层运维总闸（一键回到不装配的回测行为）。"""
    if not settings.ai_trade_horizon_backtest_enabled:
        return None
    return _resolve_horizon(raw, settings, bar=bar)


async def _resolve_account(session: AsyncSession, account_id: int | None) -> repo.OkxAccount:
    """取指定账户或回落默认账户；缺失 → 404，凭证不全 → 400（绝不假数据，对齐 trading 路由）。"""
    if account_id is not None:
        row = await repo.get_okx_account(session, account_id)
    else:
        row = await repo.get_default_okx_account(session)
    if row is None:
        raise HTTPException(status_code=404, detail={
            "code": "ACCOUNT_NOT_FOUND", "message": "账户不存在或未配置默认账户"})
    if not ((row.secret_key or "").strip() and (row.passphrase or "").strip()):
        raise HTTPException(status_code=400, detail={
            "code": "ACCOUNT_NOT_CONFIGURED", "message": "请先补全 Secret Key / Passphrase"})
    return row


async def _build_specs(session: AsyncSession,
                       strategy_ids: list[int]) -> tuple[list, dict[int, str]]:
    """逐个策略行取存在性 + `build_paper_strategy_spec` 可实例化；任一失败 → AI_TRADE_STRATEGIES_INVALID。

    **顺带算出策略指纹**（C-6，signal-prior-and-ev-gate §3.5）：本函数已逐个取过策略行，
    `template_key` / `params_json` 就在手上，零额外查询。返回 `(specs, {strategyId: 指纹})`。
    """
    specs, fingerprints = [], {}
    for sid in strategy_ids:
        row = await repo.get_strategy_row(session, sid)
        if row is None:
            raise HTTPException(400, detail={
                "code": "AI_TRADE_STRATEGIES_INVALID", "message": f"策略不存在：{sid}"})
        try:
            specs.append(build_paper_strategy_spec(row))
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            raise HTTPException(400, detail={
                "code": "AI_TRADE_STRATEGIES_INVALID",
                "message": detail.get("message", f"策略无法实例化：{sid}")}) from exc
        fingerprints[sid] = strategy_fingerprint(row.template_key, row.params_json)
    return specs, fingerprints


async def _resolve_prior_snapshots(session: AsyncSession, settings: Settings, *,
                                   inst_ids: list[str], bar: str,
                                   fingerprints: dict[int, str]) -> dict[str, dict] | None:
    """按 `instId` 逐个取先验快照并注入指纹表；总闸①关 ⇒ `None`（连查询都不发）。

    **按标的键**而非分组共享一份（P1-5）：一个分组最多 5 个标的，共享快照等于让 BTC 的成员拿
    ETH 的历史统计去开真实仓位。缺键 = 该成员无先验（不是错误，逐字回退 ATR 粗估）。

    `fingerprints` 作为**内存态键**注入快照（不落库）：`PriorRuntime` 据此做 C-6 比对，改过参数
    的策略条目会被丢弃并推一条 `AI_TRADE_PRIOR_CONTRACT_MISMATCH`。
    """
    if not settings.ai_trade_signal_prior_enabled:
        return None
    out: dict[str, dict] = {}
    for inst_id in inst_ids:
        snapshot = await prior_repo.load_prior_snapshot(session, inst_id, bar)
        if snapshot is not None:
            out[inst_id] = {**snapshot, "fingerprints": fingerprints}
    return out or None


@router.post("/ai-trade/start")
async def ai_trade_start(body: AiTradeGroupStartIn, session: AsyncSession = Depends(get_session),
                         settings: Settings = Depends(get_settings)) -> dict:
    """启动 AI 自动交易「分组」（受双闸守门；§4.1 两段式前置门禁 + 整组回滚；回执含逐成员夹紧后预算）。

    预算总额天花板由服务端解析 `cap_frac = min(maxBudgetPct/100, ai_trade_max_capital_frac(默认 1.0=不约束),
    live_auto_group_max_capital_frac)`，与非 AI 多标的分组口径统一（ai-trade-total-budget-allocation 有意放开
    历史 5% 绝对红线）：该式恒等于前端预览所读 `effectiveMaxBudgetPct/100`，故「弹窗预览 == 落地」逐配置严格
    相等。留空默认 `default_frac = ai_trade_default_capital_frac`（1%，保守）；前端任何「上限」入参一概不接受。
    成员总预算按 allocationPct 切分（非倍增）。
    """
    _require_ai_enabled(settings)
    account_row = await _resolve_account(session, body.accountId)
    specs, fingerprints = await _build_specs(session, body.strategyIds)
    priors = await _resolve_prior_snapshots(
        session, settings, inst_ids=[m.instId for m in body.members], bar=body.bar,
        fingerprints=fingerprints)
    at = await repo.get_auto_trade_settings(session)
    # 天花板委托给账户级 maxBudgetPct 与运营天花板（与 effectiveMaxBudgetPct 同源，min 只收紧不放大）；
    # ai_trade_max_capital_frac 默认 1.0=不额外约束，运维可下调为 AI 专属更紧的绝对上限。
    cap_frac = min(at["maxBudgetPct"] / 100.0,
                   settings.ai_trade_max_capital_frac,          # AI 专属可选绝对天花板（默认 1.0=不约束）
                   settings.live_auto_group_max_capital_frac)   # 分组运营天花板（与 effectiveMaxBudgetPct 同源）
    req = AiTradeGroupStartRequest(
        specs=specs,
        members_in=[AiTradeGroupMemberInput(inst_id=m.instId, allocation_pct=m.allocationPct)
                    for m in body.members],
        bar=body.bar, account_row=account_row, settings=settings,
        context_refresh_rounds=body.contextRefreshRounds, allocation_mode=body.allocationMode,
        total_capital_usd=body.capitalUsd, leverage=body.leverage,
        take_profit_usd=body.takeProfitUsd, stop_loss_usd=body.stopLossUsd,
        stop_loss_pct=body.stopLossPct, take_profit_pct=body.takeProfitPct,  # E4：单仓 OCO 入参
        priming=body.priming, priming_bars=body.primingBars,
        budget_cap_frac=cap_frac, default_budget_frac=settings.ai_trade_default_capital_frac,
        position_step_pct=_resolve_position_step(body.positionStepPct, settings),
        wake_interval_s=_resolve_wake_interval_s(body.wakeIntervalMinutes, settings),
        base_rule=_resolve_base_rules(body.baseRules, settings),
        profit_rule=_resolve_profit_guard(body.profitGuard, settings),
        horizon_rule=_resolve_horizon(body.tradingHorizon, settings, bar=body.bar),
        prior_snapshots=priors)
    try:
        await get_ai_trade_manager().start_group(req)
    except AiTradeGroupPreflightError as exc:
        # 整组前置失败须透传逐标的 members 明细（全局处理器据此包成 {error:{code,message,members}}）。
        raise HTTPException(exc.status, detail={
            "code": exc.code, "message": exc.message, "members": exc.members})
    except (AiTradeSessionLimitError, AiTradePreflightError) as exc:
        raise HTTPException(exc.status, detail={"code": exc.code, "message": exc.message})
    return get_ai_trade_manager().group_status()


@router.post("/ai-trade/{group_id}/stop")
async def ai_trade_stop(group_id: str) -> dict:
    """停整组（停所有成员、各自平自管仓、落 Run 终态；刻意不守门，确保随时可停止平仓）。"""
    result = await get_ai_trade_manager().stop_group(group_id)
    if result is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "分组不存在"})
    return result


@router.post("/ai-trade/{group_id}/member/{inst_id}/stop")
async def ai_trade_member_stop(group_id: str, inst_id: str) -> dict:
    """仅停某成员（其余续跑；不守门）；停后无运行成员则分组清空。"""
    result = await get_ai_trade_manager().stop_member(group_id, inst_id)
    if result is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "分组或成员不存在"})
    return result


@router.post("/ai-trade/{group_id}/member/{inst_id}/backstop/repair")
async def ai_trade_member_backstop_repair(group_id: str, inst_id: str) -> dict:
    """人工授权的**单次**交易所侧兜底补挂（ai-trade-backstop-repair-and-net-backtest §4.1）。

    与自动路径共用同一个 `plan_repair` 与同一条写路径，**只绕过 `mode`，绝不绕过 H1/H2/H3**。

    **刻意不守 AI 总闸**（与 `/ai-trade/{gid}/stop` 同理，D-11）：补挂是收敛风险的动作，任何时候
    都该能执行。它守的是运维总闸——总闸关 ⇒ 整层未装配 ⇒ 会话侧抛 `not_applicable` ⇒ 本处 409。

    错误映射（端点层是唯一的翻译点）：`AiTradeRepairBlocked` → 409 + `blockedReason`；
    `OKXPrivateError` → 502 + 原始 sCode + **中文原因在前**（§4.1；交易所原文常是英文，而 `plan`
    是默认模式 ⇒ 人工那一发的失败提示往往是用户对本层唯一的一次阅读，绝不能只给一串归一码）；
    分组 / 成员缺失 → 404（镜像 `stop_member`）。message 已在服务层经 `_redact` 脱敏。
    """
    try:
        result = await get_ai_trade_manager().repair_member(group_id, inst_id)
    except AiTradeRepairBlocked as exc:
        raise HTTPException(409, detail={"code": "AI_TRADE_BACKSTOP_NOT_REPAIRABLE",
                                         "message": exc.message,
                                         "blockedReason": exc.reason}) from exc
    except OKXPrivateError as exc:
        text = str(exc.message or "")
        why = repair_fail_message(exc.code)
        raise HTTPException(502, detail={
            "code": exc.code,
            "message": f"{why}：{text}" if text and why not in text else (text or why),
        }) from exc
    if result is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "分组或成员不存在"})
    return result


@router.get("/ai-trade/status")
async def ai_trade_status(settings: Settings = Depends(get_settings)) -> dict:
    """当前活跃 AI 分组（无则 group:null）；前端重载后据此重连各成员 WS（只读，不守门）。"""
    return {"group": get_ai_trade_manager().group_status(),
            "aiTradeEnabled": settings.ai_trade_enabled}


@router.get("/ai-trade/horizon-profiles", response_model=AiTradeHorizonProfilesOut)
async def ai_trade_horizon_profiles(bar: str = Query("1H"),
                                    settings: Settings = Depends(get_settings)) -> dict:
    """交易周期模式预设表（ai-trade-holding-horizon-modes §4.1）。

    **不守 AI 总闸**：读预设表不下任何单，弹窗在总闸关闭时也要能把「有哪些模式」画出来（沿用
    `priors/rebuild` 不守总闸的先例理由）。数值与中文标签**全部**由本端点下发，前端不复制任何一个
    ——本仓库已经有过前后端各存一份口径导致静默漂移的事故（`edgeSource` 逐键手抄吞新键），一张
    11 列 × 5 行的表手抄一遍必然漂移（R-3 / G-10）。

    `estimatedCostBps` 按当前配置常数估（**非实时盘口**，UI 须标「约」）：契约摘要第三行的
    「需 ≥N bps」要有一个可信来源，前端自己乘一遍就会与门禁真正判定的数分叉。
    """
    bar_seconds = BAR_SECONDS.get(bar) or BAR_SECONDS["1H"]
    # 复用**同一对**成本纯核心（不发任何请求的只读运行时，镜像 `build_charter_text` 的姿态）；
    # 取不到（总闸关 / 估算失败）⇒ None ⇒ 前端该行改述为「费率未知」，绝不编一个数。
    preview = CostRuntime(bar, settings, None, None).estimate_preview_cost(
        ord_type="market", side="long", bar_seconds=bar_seconds)
    return profiles_wire(settings,
                         estimated_cost_bps=None if preview is None else preview.total_bps)


@router.get("/ai-trade/history", response_model=AiTradeHistoryListOut)
async def ai_trade_history(limit: int = Query(20, ge=1, le=100),
                          offset: int = Query(0, ge=0),
                          session: AsyncSession = Depends(get_session)) -> dict:
    """历史 AI 自动交易会话列表（ai-trade-stats-dashboard §3.7）：按 group 聚合、startedTs DESC、
    分页；排除当前运行中分组（其在「当前」视图展示）。只读，不守门（历史查看不涉及下单）。"""
    active = get_ai_trade_manager().group_status()
    exclude = active["groupId"] if active else None
    return await repo.list_ai_trade_groups(
        session, limit=limit, offset=offset, exclude_group_id=exclude)


@router.get("/ai-trade/history/{group_id}", response_model=AiTradeHistoryDetailOut)
async def ai_trade_history_detail(group_id: str,
                                 session: AsyncSession = Depends(get_session)) -> dict:
    """单次 AI 自动交易历史详情（§3.7）：会话头 + 逐成员成交（trade_out）/ metrics + 后端聚合 KPI；组不存在 → 404。"""
    detail = await repo.get_ai_trade_group_detail(session, group_id)
    if detail is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "历史会话不存在"})
    for member in detail["members"]:
        member["trades"] = [trade_out(t) for t in member["trades"]]
    return detail


# ============================ 信号先验快照（signal-prior-and-ev-gate §4.1）============================
# 同一 `(instId, bar)` 的重建守门（进程内）：重建是秒级同步计算 + 分页取数，重入会白烧公共配额并让
# 两次写互相覆盖。只读端点不守门。
_PRIOR_REBUILD_INFLIGHT: set[tuple[str, str]] = set()


@router.post("/ai-trade/priors/rebuild", response_model=AiTradePriorSnapshotOut)
async def ai_trade_prior_rebuild(body: AiTradePriorRebuildIn,
                                 session: AsyncSession = Depends(get_session),
                                 settings: Settings = Depends(get_settings)) -> dict:
    """离线重建某 `(instId, bar)` 的先验快照（同步执行，重计算在 `run_in_threadpool` 内）。

    业务逻辑全在 `ai_auto/prior_builder`，本层只做守门与编排。**不守 AI 总闸**：重建只算统计、
    不下任何单，运维需要在开闸之前先把快照建好（§九 L1 的第一个动作）。
    """
    if body.bar not in BAR_SECONDS:
        raise HTTPException(400, detail={"code": "AI_PRIOR_BAR_INVALID",
                                         "message": f"不支持的周期：{body.bar}"})
    specs, fingerprints = await _build_specs(session, body.strategyIds)
    lookback = int(body.lookbackBars or settings.ai_trade_prior_lookback_bars)
    if lookback > settings.ai_trade_prior_max_bars:      # **取数前**快速失败，绝不无界拉 K 线
        raise HTTPException(413, detail={
            "code": "AI_PRIOR_TOO_LARGE",
            "message": (f"回看根数 {lookback} 超过上限 {settings.ai_trade_prior_max_bars}，"
                        "请缩小 lookbackBars")})
    key = (body.instId, body.bar)
    if key in _PRIOR_REBUILD_INFLIGHT:
        raise HTTPException(409, detail={"code": "AI_PRIOR_REBUILD_BUSY",
                                         "message": "该标的/周期的先验重建正在进行中"})
    _PRIOR_REBUILD_INFLIGHT.add(key)
    try:
        snapshot = await rebuild_prior_snapshot(
            inst_id=body.instId, bar=body.bar, specs=specs, fingerprints=fingerprints,
            settings=settings, lookback=lookback, segments=body.segments,
            # holding-horizon §4.5：按目标持仓模式的前瞻窗口重建（缺省仍取全局常数）。装配了周期
            # 模式的会话其 horizonBars 是 12–72，按全局 8 建的快照会被 C-3 整份丢弃——这是出路。
            horizon_bars=body.horizonBars)
    finally:
        _PRIOR_REBUILD_INFLIGHT.discard(key)
    await prior_repo.save_prior_snapshot(session, snapshot)
    return prior_snapshot_out(snapshot)


@router.get("/ai-trade/priors")
async def ai_trade_prior_get(instId: str, bar: str,
                             horizonBars: int | None = Query(None, ge=1, le=1000),
                             session: AsyncSession = Depends(get_session),
                             settings: Settings = Depends(get_settings)) -> dict:
    """读当前快照。无记录 ⇒ `{"exists": false}`——「还没建」是正常状态，不是错误（故非 404）。

    `contract` 直接把 C-2 新鲜度 / C-3 持有期的判定结果告诉运维，省得对着时间戳心算。
    可选 `horizonBars`（holding-horizon §4.5）用于**按目标周期模式核对**契约；缺省仍比全局常数
    （行为逐字不变）。
    """
    snapshot = await prior_repo.load_prior_snapshot(session, instId, bar)
    if snapshot is None:
        return {"exists": False}
    params = resolve_prior_params(settings, horizon_bars=horizonBars)
    age_s = time.time() - float(snapshot.get("builtAtTs") or 0)
    return {"exists": True, "snapshot": prior_snapshot_out(snapshot),
            "contract": {"fresh": age_s <= params.max_age_s,
                         "horizonMatch": (int(snapshot.get("horizonBars") or 0)
                                          == params.horizon_bars)}}


@router.websocket("/ws/ai-trade/{session_id}")
async def ws_ai_trade(websocket: WebSocket, session_id: str) -> None:
    """事件协议与 /ws/live-auto/{id} 同信封；连接即原子 subscribe→status→snapshot（防丢事件）。"""
    await websocket.accept()
    session = get_ai_trade_manager().get(session_id)
    if session is None:
        await websocket.send_text(json.dumps({"type": "error",
                                             "data": {"code": "NOT_FOUND", "message": "会话不存在"}}))
        await websocket.close()
        return
    queue = session.subscribe()
    snap = session.snapshot()
    try:
        await websocket.send_text(json.dumps({"type": "status", "data": snap["status"]}))
        await websocket.send_text(json.dumps({"type": "snapshot", "data": snap}))
        while True:
            event = await queue.get()
            await websocket.send_text(json.dumps(event))
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        session.unsubscribe(queue)


# ============================ AI 回测模式（ai-auto-trading-backtest §4）============================
# 回测子路由**仅**校验 Agent 已配置（在 AiBacktestManager.start 内经 resolve_active_provider），
# **不**校验 AI_TRADE_ENABLED/LIVE_TRADING_ENABLED 双闸——回测走纯模拟 SimBroker、不下真单、无真实资金面。
@router.post("/ai-trade/backtest/start")
async def ai_backtest_start(body: AiBacktestStartIn, session: AsyncSession = Depends(get_session),
                            settings: Settings = Depends(get_settings)) -> dict:
    """启动一个 AI 自动交易回测作业（§2.9 前置门禁；回执含 plannedRounds 与夹紧后生效资金/杠杆）。"""
    specs, _ = await _build_specs(session, body.strategyIds)   # 回测侧不接先验（D-2 前视偏差）
    req = AiBacktestStartRequest(
        specs=specs, inst_id=body.instId, bar=body.bar, start_ts=body.startTs, end_ts=body.endTs,
        settings=settings, context_refresh_rounds=body.contextRefreshRounds,
        decision_every_bars=body.decisionEveryBars, initial_capital=body.initialCapital,
        leverage=body.leverage, stop_loss_pct=body.stopLossPct, take_profit_pct=body.takeProfitPct,
        take_profit_usd=body.takeProfitUsd, stop_loss_usd=body.stopLossUsd,
        min_consensus=body.minConsensus,
        position_step_pct=_resolve_position_step(body.positionStepPct, settings),
        base_rule=_resolve_base_rules(body.baseRules, settings),
        profit_rule=_resolve_backtest_profit_guard(body.profitGuard, settings),
        horizon_rule=_resolve_backtest_horizon(body.tradingHorizon, settings, bar=body.bar))
    try:
        job = await get_ai_backtest_manager().start(req)
    except AiBacktestError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code, "message": exc.message})
    runner = job.runner
    return {"jobId": job.job_id, "instId": runner.inst_id, "bar": runner.bar,
            "strategyIds": runner.strategy_ids, "startTs": runner.start_ts, "endTs": runner.end_ts,
            "decisionEveryBars": runner.decision_every_bars, "plannedRounds": runner.planned_rounds,
            "initialCapital": runner.initial_capital, "leverage": runner.leverage}


@router.post("/ai-trade/backtest/{job_id}/cancel")
async def ai_backtest_cancel(job_id: str) -> dict:
    """协作式取消回测作业（不存在 → 404）；实际终态经 WS `cancelled` 事件驱动前端。"""
    outcome = get_ai_backtest_manager().cancel(job_id)
    if outcome == "unknown":
        raise HTTPException(404, detail={"code": "AI_BACKTEST_JOB_NOT_FOUND",
                                         "message": "无此回测任务"})
    job = get_ai_backtest_manager().get(job_id)
    return {"jobId": job_id, "state": job.state if job is not None else "cancelled"}


@router.get("/ai-trade/backtest/{job_id}")
async def ai_backtest_get(job_id: str) -> dict:
    """回测作业只读状态（前端重载后据此重发现 + 重连 WS）。"""
    job = get_ai_backtest_manager().get(job_id)
    if job is None:
        raise HTTPException(404, detail={"code": "AI_BACKTEST_JOB_NOT_FOUND",
                                         "message": "无此回测任务"})
    return {"job": job.status_dict()}


@router.websocket("/ws/ai-backtest/{job_id}")
async def ws_ai_backtest(websocket: WebSocket, job_id: str) -> None:
    """回测事件流（同信封 {type,data}，镜像 weight-train）：连接即原子 subscribe→snapshot 回放→续订新事件。"""
    await websocket.accept()
    job = get_ai_backtest_manager().get(job_id)
    if job is None:
        await websocket.send_text(json.dumps({
            "type": "error", "data": {"code": "AI_BACKTEST_JOB_NOT_FOUND",
                                      "message": "无此回测任务或已过期"}}))
        await websocket.close()
        return
    queue = job.subscribe()                       # subscribe() 与 snapshot_events() 间无 await：单线程下原子
    snapshot = job.snapshot_events()
    try:
        for event in snapshot:
            await websocket.send_text(json.dumps(event))
        if snapshot and snapshot[-1].get("type") in _BACKTEST_TERMINAL_TYPES:
            await websocket.close()               # 已终态（快作业先于连线结束）：回放完即关，绝不悬挂
            return
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_WS_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping"}))
                if job.is_terminal:               # 满队列若丢终态：关闭前补发 result + 终态事件
                    tail = job.snapshot_events()
                    last_result = next((e for e in reversed(tail) if e.get("type") == "result"), None)
                    if last_result is not None:
                        await websocket.send_text(json.dumps(last_result))
                    if tail and tail[-1].get("type") in _BACKTEST_TERMINAL_TYPES:
                        await websocket.send_text(json.dumps(tail[-1]))
                    await websocket.close()
                    break
                continue
            await websocket.send_text(json.dumps(event))
            if event.get("type") in _BACKTEST_TERMINAL_TYPES:
                await websocket.close()
                break
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        job.unsubscribe(queue)
