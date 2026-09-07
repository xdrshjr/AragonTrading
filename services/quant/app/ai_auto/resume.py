"""冷续跑七步流水线（ai-trade-pause-resume §3.5）。

「冷」= 软件关过（正常退出 / 强杀 / 崩溃 / 断电 / 换网络），进程里什么都没有：必须从磁盘把
契约读回来、重建会话、重新接管交易所侧那个**真实存在**的仓位。

**极性（本文件最重要的一句）：S3 的取数一律 fail-closed。**
本仓库的旁路统计（可交易性探针、兜底核实、成本估算）一律 fail-open——「探针把用户挡在门外比
给错建议更坏」。但**续跑不是旁路统计，它是一次开仓授权**：在读不到「我现在到底有没有仓、有
多大」的情况下武装一个会开真实仓位的 Agent，等于闭眼下单。故取数失败一律拒绝续跑
（`AI_TRADE_RESUME_UNVERIFIABLE`），文案给「稍后重试 / 先结束归档」两条出路。
**后人请勿「顺手统一」成 fail-open。**

续跑是**整组原子**的（[P2-13]）：任一成员 blocked ⇒ 整组拒绝，绝不「能续的先续」——预算切分、
`GroupExposureBook` 的 `unit_notional` 与验收卡口径都是分组级的，半组续跑会让这三样同时失真。
"""
from __future__ import annotations

import json
import asyncio
import logging
import time
from copy import deepcopy
from dataclasses import dataclass, field, replace

from sqlalchemy import select

from ..config import get_settings
from ..db import ai_trade_state_repositories as state_repo
from ..db.models import StrategyRun, Trade
from ..db.session import get_sessionmaker
from ..okx.trading import fetch_position_history
from ..okx.types import BAR_SECONDS
from ..live_auto.settlement import (
    SettlementQuery, SettlementValidation, resolve_settlement_increment,
    validate_settlement,
)
from ..live_auto.settlement_identity import (
    IdentityConfig, SettlementIdentityTracker, closed_epoch_contexts,
    has_closed_offline_epoch,
)
from . import pause as pause_core
from .accounting_replay import (
    TradeAccountingAccumulator, accounted_close_pnl,
    is_continuity_aligned, trade_meta,
)
from .experience.adopted import AdoptedOpen, synth_from_contract
from .manager import (AiTradeGroup, AiTradeManager, AiTradePreflightError,
                      _creds)
from .reconcile import local_net_view
from .trade_economics import finite
from .trade_persistence import append_trade_once

logger = logging.getLogger(__name__)


class AiTradeResumeBlocked(AiTradePreflightError):
    """续跑被离线差异判定挡下（携逐成员 `.members` 明细，格式与 `AI_TRADE_GROUP_PREFLIGHT` 一致）。"""

    def __init__(self, members: list[dict], message: str = "部分标的无法继续 AI 自动交易"):
        self.members = members
        super().__init__("AI_TRADE_RESUME_BLOCKED", message, 409)


@dataclass
class _Assessment:
    """S1~S4 的产物（`resume-preview` 与 `resume` **共用同一条**评估路径）。"""

    row: dict
    req: object
    creds: object
    avail: float = 0.0
    positions: list[dict] = field(default_factory=list)
    configs: dict = field(default_factory=dict)
    pending: list[dict] = field(default_factory=list)
    members: list[dict] = field(default_factory=list)
    verdict: str = pause_core.GROUP_READY


# ---------- S1 / S2：契约与解析链 ----------
async def _load_contract(group_id: str, settings) -> dict:
    """S1：读状态行并校验可续跑（不存在 ⇒ 404；非 paused ⇒ 409；超龄 ⇒ 只许归档）。"""
    async with get_sessionmaker()() as db:
        row = await state_repo.load_group_state(db, group_id)
    if row is None:
        raise AiTradePreflightError("NOT_FOUND", "分组不存在", 404)
    has_pending = any(state_repo._has_pending(m) for m in row["members"])
    if row["status"] != pause_core.STATUS_PAUSED and not has_pending:
        raise AiTradePreflightError("AI_TRADE_NOT_PAUSED", "该分组不处于已暂停状态", 409)
    if not has_pending and pause_core.is_age_exceeded(row["pausedTs"], int(time.time()),
                                  float(settings.ai_trade_resume_max_age_s)):
        raise AiTradePreflightError(
            "AI_TRADE_RESUME_EXPIRED",
            "这个暂停契约已超过可续跑时限，只能结束归档（如仍有持仓，请到实盘面板自行处理）", 409)
    return row


async def _rebuild_request(row: dict, settings):
    """S2：用 `start_wire` **重跑一遍** `/ai-trade/start` 的解析链（绝不另写一份）。

    延迟导入 `api.ai_auto` 是刻意的：那是唯一一条解析链的所在地，而 `api` 依赖 `ai_auto`——
    在函数体内导入既复用了它，又不制造模块层循环依赖（同 `manager._require_capacity` 的既有姿态）。

    ⚠️ **冷续跑不经轴 A-1 互锁**（loss-and-liveness-hardening 评审 P0-3 / `G-A8` 冻结这一条）：
    `_require_profit_exit_with_horizon` 落在 `ai_trade_start` **端点内**，本函数调用的是
    `build_start_request` 本身，故那条 422 在这条路径上结构性地不可达。这不是疏忽而是设计：
    下面的 `except HTTPException` 会把**任何** 422 一律翻成 409「启动契约已失效，无法继续」，
    而生产里有 **50 条**「周期已装配 + 保护层未装配」的会话——把互锁挪进装配链，它们一旦暂停
    就再也续不回来，而续不回来的会话可能正持有真实仓位。
    互锁的职责是「不让新的致命会话被创建」，**不是「把已经存在的会话锁死」**。
    """
    from fastapi import HTTPException

    from ..api.ai_auto import build_start_request
    from ..schemas import AiTradeGroupStartIn

    try:
        body = AiTradeGroupStartIn(**(row["startWire"] or {}))
        async with get_sessionmaker()() as db:
            req = await build_start_request(db, settings, body)
    except HTTPException as exc:      # 账户凭证被清 / 策略被删 ⇒ 契约已失效，明确原因
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        raise AiTradePreflightError(
            "AI_TRADE_RESUME_CONTRACT_INVALID",
            f"启动契约已失效，无法继续：{detail.get('message') or exc.detail}", 409) from exc
    except Exception as exc:  # noqa: BLE001 —— 契约损坏也必须给出可读原因，绝不 500
        raise AiTradePreflightError(
            "AI_TRADE_RESUME_CONTRACT_INVALID", f"启动契约已失效，无法继续：{exc}", 409) from exc
    req.resume_of = {"groupId": row["groupId"], "createdTs": row["createdTs"],
                     "resumedCount": row["resumedCount"],
                     "runIds": {str(m.get("instId")): m.get("runId")
                                for m in row["members"] if m.get("runId")}}
    return req


# ---------- S3 / S4：交易所真值与差异判定 ----------
async def assess_group(manager: AiTradeManager, group_id: str) -> _Assessment:
    """S1~S4：契约 → 解析链 → 交易所真值 → 逐成员 verdict + 组级 verdict。"""
    settings = get_settings()
    row = await _load_contract(group_id, settings)
    row = await _retry_pending_trade_contract(row)
    req = await _rebuild_request(row, settings)
    creds = _creds(req.account_row)
    assessment = _Assessment(row=row, req=req, creds=creds)
    try:
        avail, positions, configs, pending = await manager._gather_context(
            creds, req, req.members_in)
    except Exception as exc:  # noqa: BLE001 —— S3 fail-closed（见模块 docstring）
        logger.warning("ai-trade resume exchange probe failed (%s): %s", group_id, exc)
        raise AiTradePreflightError(
            "AI_TRADE_RESUME_UNVERIFIABLE",
            "无法向交易所核实当前持仓与挂单，暂不继续（请稍后重试，或先结束归档）", 409) from exc
    assessment.avail, assessment.positions = avail, positions
    assessment.configs, assessment.pending = configs, pending
    assessment.members = _classify_members(row, positions, pending, settings)
    await _assess_pending_attempts(assessment)
    assessment.verdict = pause_core.group_verdict(
        [m["verdict"] for m in assessment.members],
        [m.get("blockedReason") for m in assessment.members])
    return assessment


async def _retry_pending_trade_contract(row: dict) -> dict:
    """外部预检前先补齐原始腿，避免把已确认平仓当成离线缺口再合成。"""
    group_id = row["groupId"]
    try:
        async with get_sessionmaker()() as db:
            success = await asyncio.wait_for(
                state_repo.retry_pending_trades(db, group_id), timeout=5.0)
            if not success:
                raise ValueError("成交恢复时分组契约不存在")
            updated = await state_repo.load_group_state(db, group_id)
            if updated is None:
                raise ValueError("成交恢复后分组契约不存在")
            return updated
    except Exception as exc:
        logger.warning("ai-trade pending trade replay failed (%s): %s", group_id, exc)
        raise AiTradePreflightError(
            "AI_TRADE_RESUME_UNVERIFIABLE",
            "原始成交尚未完成持久化，暂不继续（请稍后重试）", 409) from exc


async def _assess_pending_attempts(assessment: _Assessment) -> None:
    """预检只查询固定ID，未找到或身份不符不能当作无挂单。"""
    from ..okx.trading import fetch_live_order
    from .open_attempts import OpenAttemptManager
    from types import SimpleNamespace
    contracts = {m["instId"]: m for m in assessment.row["members"]}
    for member in assessment.members:
        contract = contracts[member["instId"]]
        runtime = contract.get("runtimeIntegrity") or {}
        if not state_repo._has_pending(contract):
            continue
        attempts = OpenAttemptManager.restore(
            runtime, account_id=assessment.row["accountId"],
            run_id=contract["runId"], inst_id=member["instId"],
            max_per_session=50)
        attempts.restore_resting(SimpleNamespace(resting=None))
        pending = attempts.pending
        state = None
        if pending is not None:
            try:
                state = await fetch_live_order(
                    assessment.creds, member["instId"], cl_ord_id=pending.cl_ord_id)
            except Exception as exc:
                logger.warning("pending open preview unavailable: %s", exc)
        matched = pending is not None and attempts._matches(pending, state)
        aligned = pending is not None and _pending_position_matches(
            member, pending, state or {}, contract)
        if matched and aligned:
            required = assessment.req.settings.ai_trade_resume_backstop_required
            verdict, blocked = pause_core.apply_backstop_requirement(
                pause_core.VERDICT_MATCHED, member.get("backstop"),
                required=bool(required and member.get("actual")))
            member.update(verdict=verdict, blockedReason=blocked,
                          message="固定订单身份已核实，恢复时接管")
        else:
            member.update(verdict=pause_core.VERDICT_UNKNOWN_ORDER,
                          blockedReason="acceptance_unknown",
                          message="开仓委托仍待核实，保留原订单身份")


def _pending_position_matches(member, pending, state, contract) -> bool:
    """固定订单只能解释自身成交，不能解除方向、模式及其他委托冲突。"""
    from ..live_auto.resting import classify_state

    if classify_state(str(state.get("state") or "")) == "unknown":
        return False
    if member.get("blockedReason"):
        return False
    if member.get("verdict") in {
        pause_core.VERDICT_SIDE_FLIPPED, pause_core.VERDICT_FOREIGN_POSITION,
    }:
        return False
    known = set(contract.get("knownClOrdIds") or []) | {pending.cl_ord_id}
    if any(str(o.get("clOrdId") or "") not in known
           for o in member.get("pendingOrders") or []):
        return False
    filled = finite(state.get("accFillSz"))
    actual = member.get("actual") or {}
    quantity = finite(actual.get("pos", 0))
    if filled is None or quantity is None or filled < 0:
        return False
    if abs(quantity) > 0:
        side = pause_core._side_from_okx(actual)
        if side != pending.order["direction"]:
            return False
        if actual.get("mgnMode") not in (None, pending.order.get("td_mode")):
            return False
    return abs(abs(quantity) - filled) <= max(1e-10, filled * 1e-8)


def _classify_members(row: dict, positions: list[dict], pending: list[dict],
                      settings) -> list[dict]:
    """逐成员过判定矩阵（纯核心 `pause.classify_member_drift`），再叠 `backstop_required` 读点。"""
    by_inst = {str(p.get("instId") or ""): p for p in positions or []
               if abs(float(p.get("pos") or 0.0)) > 0}
    out = []
    for member in row["members"]:
        inst = str(member.get("instId") or "")
        actual = by_inst.get(inst)
        drift = pause_core.DriftInput(
            expected=member.get("position"), actual=actual,
            pending=[o for o in (pending or []) if str(o.get("instId") or "") == inst],
            known_cl_ord_ids=frozenset(member.get("knownClOrdIds") or []),
            paused_ts=int(row.get("pausedTs") or 0),
            size_tol=float(settings.ai_trade_resume_size_tolerance),
            position_unknown=bool(member.get("positionUnknown")))
        verdict, message = pause_core.classify_member_drift(drift)
        # `ai_trade_resume_backstop_required` 的读点（[P2-6]）：矩阵判完、组级 verdict 之前。
        # 冷续跑此刻还没有向交易所发过兜底核实，故用**关机前最后一次**的结果（`backstopAtPause`）
        # ——它是这条路径上唯一诚实可得的数据，UI 必须把它标成过去时（§3.9.2 [P2-9]）。
        backstop = member.get("backstopAtPause")
        verdict, blocked = pause_core.apply_backstop_requirement(
            verdict, backstop, required=bool(settings.ai_trade_resume_backstop_required))
        out.append({"instId": inst, "runId": member.get("runId"), "verdict": verdict,
                    "message": message, "expected": member.get("position"),
                    "actual": actual, "backstop": backstop,
                    "pendingOrders": drift.pending, "blockedReason": blocked})
    return out


def preview_wire(assessment: _Assessment) -> dict:
    """`GET /ai-trade/{gid}/resume-preview` 的回执（§5.2）。判定性文案全部由后端下发。"""
    row = assessment.row
    now = int(time.time())
    offline = max(0, now - int(row.get("pausedTs") or 0)) if row.get("pausedTs") else 0
    req = assessment.req
    echo = row.get("resolvedEcho") or {}
    return {
        "groupId": row["groupId"], "pausedTs": row.get("pausedTs"), "asOfTs": now,
        "offlineSeconds": offline, "offlineText": pause_core.describe_offline_span(offline),
        "ageExceeded": False, "resumedCount": row.get("resumedCount", 0),
        "pauseReason": row.get("pauseReason"),
        "pauseReasonText": pause_core.PAUSE_REASON_TEXT.get(row.get("pauseReason") or "", ""),
        "contract": {
            "bar": row.get("bar"),
            "instIds": [m.inst_id for m in req.members_in],
            "totalBudgetUsd": echo.get("totalBudgetUsd"),
            "leverage": echo.get("leverage") or req.leverage,
            "stopLossPct": req.stop_loss_pct, "takeProfitPct": req.take_profit_pct,
            "disciplinePreset": req.discipline_preset,
            "strategyNames": [s.strategy_name for s in req.specs],
        },
        "drift": _contract_drift(echo, req),
        "members": assessment.members,
        "verdict": assessment.verdict,
        "blockedReason": next((m["blockedReason"] for m in assessment.members
                               if m.get("blockedReason")), None),
    }


def _contract_drift(echo: dict, req) -> list[dict]:
    """契约漂移清单：落地回执快照 vs 本次重解析结果（服务端默认变了就必须说出来，D-2 / 风险 3）。"""
    drift = []
    for field_name, before, after in (
            ("discipline.preset", (echo.get("discipline") or {}).get("preset"),
             req.discipline_preset),
            ("leverage", echo.get("leverage"), req.leverage),
            ("totalBudgetUsd", echo.get("totalBudgetUsd"), req.total_capital_usd)):
        if before is not None and after is not None and before != after:
            drift.append({"field": field_name, "from": before, "to": after,
                          "reason": "服务端解析结果与上次启动时不同，续跑将按新值生效"})
    return drift


# ---------- S5 ~ S7：装配 → 接管 → 起会话 ----------
async def resume_cold_group(manager: AiTradeManager, group_id: str, *,
                            acknowledged: bool) -> AiTradeGroup:
    """冷续跑主入口（由 `AiTradeManager.resume_group` 在准入锁内调用）。"""
    assessment = await assess_group(manager, group_id)
    if assessment.verdict == pause_core.GROUP_BLOCKED:
        raise AiTradeResumeBlocked(assessment.members)
    if assessment.verdict == pause_core.GROUP_NEEDS_CONFIRM and not acknowledged:
        raise AiTradePreflightError(
            "AI_TRADE_RESUME_NOT_ACKNOWLEDGED",
            "离线期间该分组的持仓发生了变化，请先在复核框里确认后再继续", 400)
    req = assessment.req
    group = await _build_resumed_group(manager, assessment)
    try:                                                        # S7：起会话（整组回滚）
        await _adopt_group(group, assessment)
        for member in group.members:
            await member.session.start()
    except (Exception, asyncio.CancelledError):
        for member in group.members:
            # **先摘掉 W5 回调**：那条回调是给「一个已经在跑的会话自终止」用的，会把契约置
            # `ended`。而续跑失败时契约必须**留在 paused**（S7：可再试）——这些会话根本没起来，
            # 交易所上不可能有它们开出的仓位，没有任何东西需要被收敛。
            member.session.on_stopped = None
            try:
                await member.session.stop("resume_failed", flatten=False)
            except (Exception, asyncio.CancelledError):
                pass
        raise
    _adopt_entry_bars(group, assessment)
    await _mark_runs_running(group)
    group.resumed_count = int((req.resume_of or {}).get("resumedCount") or 0) + 1
    group.state = pause_core.STATUS_RUNNING
    return group


async def _build_resumed_group(manager: AiTradeManager,
                               assessment: _Assessment) -> AiTradeGroup:
    """S5：复用 `_build_member` 的**同一条**装配路径（只把「建 run」换成「取既有 run」）。

    D-6：**不能走 `start_group`** —— `_member_preflight_fail` 的「该标的已有持仓」这一条会 100%
    拒绝，而「带仓暂停后续跑」的定义就是该标的上有持仓。故此处把 `positions` / `pending` 传空，
    使那两条闸自然直通；**其余各条门禁（不支持的标的 / 预算过小）一条不放**。
    """
    from ..agent.providers import resolve_active_provider

    req = assessment.req
    provider = await resolve_active_provider(get_sessionmaker(), req.settings)
    if provider is None:
        raise AiTradePreflightError(
            "AGENT_NOT_CONFIGURED", "未配置 AI 模型，无法继续（请在设置中心配置默认模型）", 400)
    lever = req.leverage or req.settings.live_default_leverage
    total = manager._resolve_budget_group(req.total_capital_usd, assessment.avail,
                                          req.budget_cap_frac, req.default_budget_frac)
    plans = manager._preflight_group(req.members_in, assessment.configs, [], total, lever, [])
    return await manager._build_group(req, assessment.creds, provider, plans, total, lever)


async def _adopt_group(group: AiTradeGroup, assessment: _Assessment) -> None:
    """先恢复完整会计，再补离线腿并二次恢复，最后接管真实持仓。"""
    assessed = {m["instId"]: m for m in assessment.members}
    contracts = {m["instId"]: m for m in assessment.row["members"]}
    for member in group.members:
        session = member.session
        contract = contracts.get(member.inst_id) or {}
        session.started_ts = int(assessment.row.get("createdTs")
                                 or session.started_ts)
        session._known_cl_ord_ids = list(contract.get("knownClOrdIds") or [])
        continuity = await rebuild_continuity(member.run_id, net_mode=None)
        _restore_cost_basis(session.executor, continuity.get("costBasis"))
        await _restore_runtime_integrity(session, contract, continuity)
        restore_member_continuity(member, continuity, contract)
        current = assessed.get(member.inst_id) or {}
        if current.get("verdict") == pause_core.VERDICT_CLOSED_OFFLINE:
            await settle_closed_offline(member, contract,
                creds=assessment.creds, cost_basis=continuity.get("costBasis"),
                paused_ts=int(assessment.row.get("pausedTs") or 0))
            continuity = await rebuild_continuity(member.run_id, net_mode=None)
            restore_member_continuity(member, continuity, contract)
        adopt_position(session.executor, current.get("actual"))
        await _restore_pending_state(member)
        continuity = await rebuild_continuity(member.run_id, net_mode=None)
        if not is_continuity_aligned(continuity, session.executor.projected,
                                     session.executor.entry_qty_sz):
            raise AiTradeResumeBlocked([{"instId": member.inst_id,
                "blockedReason": "settlement_unresolved",
                "message": "仓位与已保存成交不一致，核实缺失成交后才能继续"}])
        experience = _experience(member)
        if experience is not None:
            experience.adopt_open(_adopted_open(
                member, current.get("actual"), continuity["entryFeeUsd"]))
        session._last_status_side = session.executor.projected
        await _restore_summary(session, member.run_id)


def restore_member_continuity(member, continuity: dict, contract: dict) -> None:
    """统一恢复完整成交、费池、seq和观察；补记后仍调用同一写点。"""
    session, trades = member.session, continuity["trades"]
    executor = session.executor
    executor.restore_trades(trades)
    executor.realized_pnl_usd = continuity["realizedPnlUsd"]
    session._position_seq = continuity["positionSeq"]
    if hasattr(session, "_cost"):
        session._cost.restore_accounting(trades, {
            "runId": member.run_id, "costBasis": continuity.get("costBasis"),
            "ctVal": executor.ct_val})
    executor.entry_fee_usd = continuity["entryFeeUsd"]
    runtime = contract.get("runtimeIntegrity") or {}
    observation = runtime.get("settlementContext")
    session._settlement_observation = deepcopy(observation)
    session._settlement_revision = runtime.get("settlementStateRevision", 0)
    tracker = getattr(executor, "settlement_identity", None)
    if tracker is not None:
        tracker.restore(trades, observation)


async def _restore_pending_state(member) -> None:
    """恢复挂单身份后才允许会话启动，不创建替代订单。"""
    session, executor = member.session, member.session.executor
    attempts = getattr(executor, "open_attempts", None)
    if attempts is not None and not attempts.restore_resting(executor):
        raise AiTradeResumeBlocked([{
            "instId": member.inst_id, "blockedReason": "acceptance_unknown",
            "message": "原挂单身份不可读，无法安全恢复"}])
    if not hasattr(session, "_resolve_pending_open"):
        return
    async with session._exec_lock:
        await session._resolve_pending_open(int(time.time()))
    if attempts is not None and attempts.has_pending:
        raise AiTradeResumeBlocked([{
            "instId": member.inst_id, "blockedReason": "acceptance_unknown",
            "message": "原订单尚未完成核查和持久化接管"}])


async def _restore_summary(session, run_id: int) -> None:
    """摘要能力版本不能替代已保存摘要的真实来源版本。"""
    snapshot = await _run_snapshot(run_id)
    session.rounds_completed = int(snapshot.get("roundsCompleted") or 0)
    seed = {"summary": snapshot.get("finalSummary"),
            "last_round": session.rounds_completed}
    if hasattr(session.decider, "memory_wire"):
        memory = ((snapshot.get("aiStats") or {}).get(
            "integrity") or {}).get("memory") or {}
        seed.update(summary_as_of_ts=memory.get("summaryAsOfTs"),
                    facts_version=memory.get("factsVersion"),
                    evidence_version=memory.get("summaryEvidenceVersion"),
                    legacy_narrative=bool(memory.get("legacyNarrative")))
    session.decider.seed_from_snapshot(**seed)


def _restore_cost_basis(executor, basis: str | None) -> None:
    """同一 run 继续使用建组时记账口径，设置变更只影响新组。"""
    if basis not in {"net", "gross"}:
        return
    executor.settings = executor.settings.model_copy(update={
        "ai_trade_net_pnl_enabled": basis == "net"})
    if executor.fee_ledger is not None:
        executor.fee_ledger.params = replace(
            executor.fee_ledger.params, net_pnl_enabled=basis == "net")


async def _restore_runtime_integrity(session, contract: dict,
                                     continuity: dict) -> None:
    """先恢复额度和受控订单身份，再重放全量成交采纳水位。"""
    if not hasattr(session, "_entry_quota"):
        return
    from .open_attempts import OpenAttemptManager
    snapshot = await _run_snapshot(session.run_id)
    legacy_used = ((snapshot.get("aiStats") or {}).get("openFunnel") or {}).get(
        "contrarianPassed", 0)
    attempts = OpenAttemptManager.restore(
        contract.get("runtimeIntegrity"), account_id=session.executor.account_id,
        run_id=session.run_id, inst_id=session.inst_id,
        max_per_session=session._consensus_rule.contrarian_max_per_session,
        persist_callback=session._persist_runtime_integrity,
        legacy_used=legacy_used)
    session.executor.open_attempts = attempts
    session._entry_quota = attempts.quota
    session._contrarian_used = attempts.quota.used
    session._consensus_rule = replace(
        session._consensus_rule,
        contrarian_max_per_session=attempts.quota.max_per_session)


def _experience(member):
    """成员会话的经验层门面；未装配 ⇒ `None`。

    **极性说明（本文件的 fail-closed 只管取数，不管旁路观测）**：续跑本身是一次开仓授权，
    所以 S3 的取数一律 fail-closed。但经验采集是**旁路观测**——它缺席的最坏后果是「这一笔往返
    没进经验库」，而把它变成一个能挡下合法续跑的异常，等于让一个统计增强读数决定真实资金能不能
    继续交易。沿用 `experience.runtime.persist_pending` 的 best-effort 姿态。
    """
    return getattr(getattr(member, "session", None), "experience", None)


def _adopted_open(member, actual: dict | None,
                  entry_fee_usd: float | None) -> AdoptedOpen | None:
    """接管持仓使用共享费池；未知、零费和负返佣均保留原值。"""
    executor = member.session.executor
    if executor.projected == "flat" or executor.entry_price <= 0 or executor.entry_qty_sz <= 0:
        return None
    return AdoptedOpen(
        seq=int(member.session._position_seq), side=str(executor.projected),
        entry={"px": float(executor.entry_price), "qty": float(executor.entry_qty_sz),
               # 与平仓 trade 的 `ts` 同一时钟（墙钟秒）：交易所 `cTimeS` 是权威开仓时刻。
               "ts": int((actual or {}).get("cTimeS") or 0)},
        open_fee_usd=finite(entry_fee_usd),
        ct_val=float(getattr(executor, "ct_val", 0.0) or 0.0),
        horizon_profile_id=getattr(member.session.horizon_rule, "profile_id", None))


async def settle_closed_offline(member, contract: dict, *, creds,
                                cost_basis: str | None, paused_ts: int) -> None:
    """离线只补已认证结算；任何缺口都保留暂停契约，禁止新仓越过旧仓。"""
    full = await rebuild_continuity(member.run_id, net_mode=None)
    tracker = _offline_tracker(member, contract, full["trades"])
    context, executor = tracker.context(), member.session.executor
    if context is None and _offline_already_settled(
            tracker, contract, full["trades"]):
        latest = next((trade_meta(t).get("settlementValidation")
            for t in reversed(full["trades"]) if t["side"] == "close"), None)
        await _mark_offline_gap(member.run_id, member.inst_id, latest)
        return
    tol = executor.settings.ai_trade_settlement_match_tol_s
    query = SettlementQuery(member.inst_id,
        context.direction if context else "", 0, int(time.time()),
        context=context, tol_s=tol, mode="offline", paused_ts=paused_ts)
    rows = []
    try:
        rows = await fetch_position_history(creds, member.inst_id,
            after_ms=max(0, int(max(0, paused_ts - tol) * 1000) - 1))
        validation = validate_settlement(rows, query, tracker.claimed,
                                         int(time.time()))
    except Exception:
        logger.warning("offline exit readback failed: %s", member.inst_id)
        validation = SettlementValidation(status="unavailable",
            reason="fetch_failed", as_of_ts=int(time.time()),
            epoch_key=context.epoch_key if context else None)
    trade = _offline_exit_trade(validation, context, cost_basis=cost_basis)
    experience = _experience(member)
    if experience is not None:
        experience.adopt_open(synth_from_contract(contract,
            seq=int(member.session._position_seq), ct_val=executor.ct_val,
            open_fee_usd=full["entryFeeUsd"]))
    if trade is None:
        if validation.status == "matched":
            validation = replace(validation, status="unavailable",
                                 reason="increment_invalid", fact=None)
        await _mark_offline_gap(member.run_id, member.inst_id, validation)
        if experience is not None:
            await experience.note_offline_close(None, has_row=bool(rows))
        raise AiTradeResumeBlocked([{"instId": member.inst_id,
            "blockedReason": "settlement_unresolved",
            "message": "离线结算尚未核实，请稍后重试",
            "settlementValidation": validation.wire()}])
    async with get_sessionmaker()() as db:
        added = await append_trade_once(db, member.run_id, trade)
    await _mark_offline_gap(member.run_id, member.inst_id,
        replace(validation, fee_adjustment=trade_meta(trade)["feeAdjustment"]))
    if added and experience is not None:
        await experience.note_offline_close(trade, has_row=True)


def _offline_tracker(member, contract: dict, trades: list[dict]):
    executor = member.session.executor
    current = getattr(executor, "settlement_identity", None)
    config = current.config if current is not None else IdentityConfig(
        executor.account_id, member.run_id, member.inst_id,
        {"marginMode": executor.td_mode})
    tracker = SettlementIdentityTracker(config)
    observation = (contract.get("runtimeIntegrity") or {}).get(
        "settlementContext")
    tracker.restore(trades, observation)
    return tracker


def _offline_already_settled(tracker, contract: dict, trades: list) -> bool:
    """兼容旧别名须先证明暂停契约对应完整DB中的同一个已闭合周期。"""
    observed = (contract.get("runtimeIntegrity") or {}).get(
        "settlementContext") or {}
    epoch = observed.get("epochKey")
    expected = contract.get("position") or {}
    if not epoch:
        seq = expected.get("positionSeq")
        contexts = closed_epoch_contexts(trades, tracker.config)
        if type(seq) is not int or not 0 <= seq < len(contexts):
            return False
        context = contexts[seq]
        if (finite(expected.get("openWallTs")) is None
                or expected["openWallTs"] != context.first_open_wall_ts
                or expected.get("side") != context.direction
                or finite(expected.get("size")) != context.remaining_qty):
            return False
        epoch = context.epoch_key
    if any(claim.get("epochKey") == epoch for claim in tracker.claimed.values()):
        return True
    return has_closed_offline_epoch(trades, tracker.config, epoch,
                                   observed.get("posId") or expected.get("posId"))


def _offline_exit_trade(validation, context, *, cost_basis) -> dict | None:
    """仅将认证 fact 换为增量成交，价量与费用、盈亏共用一个结果。"""
    if not isinstance(validation, SettlementValidation):
        return None
    if validation.status != "matched":
        return None
    result = resolve_settlement_increment(validation.fact, context, cost_basis)
    increment = result.increment
    if increment is None:
        return None
    validated = replace(validation, fee_adjustment=increment.fee_adjustment,
                        price_basis=increment.price_basis)
    return {"ts": int(validation.fact.close_ts), "side": "close",
            "price": increment.price, "qty": increment.qty,
            "fee": increment.fee, "pnl": increment.pnl,
            "positionAfter": 0.0, "reason": "offline_exit",
            "explain": {"kind": "offline_exit",
                "headline": "离线期间该仓已在交易所侧平掉",
                "summary": "续跑前核实本次持仓的交易所结算",
                "meta": _offline_exit_meta(validated, increment)}}


def _offline_exit_meta(validation, increment) -> dict:
    fact = validation.fact
    return {"source": "ai", "exitSource": "offline",
            "posId": fact.pos_id, "wallTs": fact.close_ts,
            "settlementValidation": validation.wire(),
            "settlementKey": fact.settlement_key,
            "accountingEventId": increment.accounting_event_id,
            "accountingVersion": 2, "feeKnown": True,
            "feeCurrency": "USDT", "feeBasis": increment.fee_basis,
            "feeAdjustment": increment.fee_adjustment,
            "priceBasis": increment.price_basis,
            "offlineFundingUsd": fact.funding_usd,
            "offlineLiqPenaltyUsd": fact.liq_usd,
            "offlineSettledPnlUsd": fact.settled_pnl_usd}


async def _mark_offline_gap(run_id: int, inst_id: str, validation) -> None:
    """同一简报保存缺口与成功恢复；不将未认证金额写作零成交。"""
    from ..db import repositories as repo

    metrics = await _run_snapshot(run_id)
    stats = metrics.get("aiStats")
    metrics["aiStats"] = stats = stats if isinstance(stats, dict) else {}
    wire = (validation.wire() if isinstance(validation, SettlementValidation)
            else validation)
    stats.setdefault("integrity", {"schemaVersion": 2})[
        "settlementValidation"] = wire
    if wire is None or wire.get("status") == "matched":
        stats.pop("offlineGap", None)
    else:
        stats["offlineGap"] = {"instId": inst_id,
            "verdict": "closed_offline", "pnlUnknown": True,
            "reason": wire.get("reason")}
    async with get_sessionmaker()() as db:
        await repo.finalize_run(db, run_id, status="paused", metrics=metrics)


def _cost_basis(session) -> str:
    """该 run 的盈亏口径（`settings.ai_trade_net_pnl_enabled` 的既有落库口径，绝不另判一次）。"""
    return "net" if session.executor.settings.ai_trade_net_pnl_enabled else "gross"


def adopt_position(executor, actual: dict | None) -> None:
    """接管持仓：取**交易所真值**（不取快照）——用快照会在「离线期间部分平仓」时高估持仓。"""
    if not actual:
        executor.projected = "flat"
        executor.entry_price = 0.0
        executor.entry_qty_sz = 0.0
        return
    pos = float(actual.get("pos") or 0.0)
    side = str(actual.get("posSide") or "").lower()
    if side not in ("long", "short"):
        side = "long" if pos > 0 else "short"
    executor.projected = side
    executor.entry_price = float(actual.get("avgPx") or 0.0)
    executor.entry_qty_sz = abs(pos)


def _adopt_entry_bars(group: AiTradeGroup, assessment: _Assessment) -> None:
    """`entry_bar` 反推（须在 `start()` 之后——窗口是暖机时才填的）。

    `openWallTs` 的取值顺序（[P2-14]）：① 交易所真值 `cTimeS`（**首选**）；② 本地快照的
    `openWallTs`；③ 都没有则回落 `cur_index`。优先用 `cTime` 的理由：离线期间「先加仓再减回
    同样张数」会让本地 `wallTs` 指向一个早已不存在的开仓腿，而 `cTime` 是交易所对**这个 posId**
    的权威开仓时刻。不做的话 `bars_in_position` 从 0 起算 ⇒ 时间强平被推迟一整个持仓周期。
    """
    contract = {str(m.get("instId") or ""): m for m in assessment.row["members"]}
    by_inst = {m["instId"]: m for m in assessment.members}
    now = int(time.time())
    for member in group.members:
        executor = member.session.executor
        if executor.projected == "flat":
            continue
        cur_index = member.session._window.cur_index()
        actual = (by_inst.get(member.inst_id) or {}).get("actual") or {}
        snapshot = (contract.get(member.inst_id) or {}).get("position") or {}
        open_ts = int(actual.get("cTimeS") or snapshot.get("openWallTs") or 0)
        if open_ts <= 0:
            executor.entry_bar = cur_index
            continue
        bar_seconds = BAR_SECONDS.get(member.session.bar, 60) or 60
        executor.entry_bar = max(0, cur_index - max(0, now - open_ts) // bar_seconds)


async def rebuild_continuity(run_id: int, *, net_mode: bool | None) -> dict:
    """从完整成交重建序号、余量、费池与原run口径盈亏，缺口保留未知。"""
    async with get_sessionmaker()() as db:
        run = await db.get(StrategyRun, run_id)
        config = _parse_json(getattr(run, "config_json", None))
        rows = (await db.execute(
            select(Trade).where(Trade.run_id == run_id)
            .order_by(Trade.id.asc()))).scalars().all()
    basis = config.get("costBasis")
    if basis in ("net", "gross"):
        net_mode = basis == "net"
    trades = [{"id": r.id, "runId": r.run_id,
               "ts": r.ts, "side": r.side, "price": r.price, "qty": r.qty,
               "fee": r.fee, "pnl": r.pnl, "explain": _parse_json(r.explain_json),
               "positionAfter": r.position_after, "reason": r.reason}
              for r in rows]
    accounting = TradeAccountingAccumulator({"runId": run_id,
        "costBasis": basis if basis in ("net", "gross") else None})
    seq, realized = 0, 0.0
    for trade in trades:
        result = accounting.record_trade(trade, epoch_id=seq)
        if result.get("duplicate"):
            continue
        if result.get("complete"):
            seq += 1
        if trade["side"] == "close":
            pnl = (None if result.get("reason") else
                   accounted_close_pnl(trade, basis))
            realized = (realized + pnl
                        if realized is not None and pnl is not None else None)
    return {"trades": trades,
            "costBasis": basis if basis in ("net", "gross") else None,
            "realizedPnlUsd": realized,
            "tradingNetUsd": local_net_view(trades, net_mode=net_mode)["netUsd"],
            "positionSeq": seq,
            "remainingQty": accounting.remaining_qty,
            "positionSide": accounting.position_side,
            "entryFeeUsd": accounting.remaining_open_fee_usd}


def _parse_json(raw: str | None) -> dict:
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _run_snapshot(run_id: int) -> dict:
    """该 run 已落库的 metrics（滚动摘要 + 轮次）；缺失 / 坏 JSON ⇒ 空壳。"""
    import json
    async with get_sessionmaker()() as db:
        run = await db.get(StrategyRun, run_id)
        raw = getattr(run, "metrics_json", None) if run is not None else None
    try:
        metrics = json.loads(raw or "")
    except (TypeError, ValueError):
        return {}
    return metrics if isinstance(metrics, dict) else {}


async def _mark_runs_running(group: AiTradeGroup) -> None:
    """S7 收尾：把逐成员 run 的状态从 `paused` 拨回 `running`（不新建 run，I-3）。"""
    from ..db import repositories as repo

    async with get_sessionmaker()() as db:
        for member in group.members:
            await repo.finalize_run(db, member.run_id, status="running")
