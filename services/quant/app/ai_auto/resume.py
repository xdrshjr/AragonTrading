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

import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import select

from ..config import get_settings
from ..db import ai_trade_state_repositories as state_repo
from ..db.models import StrategyRun, Trade
from ..db.session import get_sessionmaker
from ..okx.trading import fetch_position_history
from ..okx.types import BAR_SECONDS
from . import pause as pause_core
from .experience.adopted import AdoptedOpen, synth_from_contract
from .manager import (AiTradeGroup, AiTradeManager, AiTradePreflightError,
                      _creds)
from .reconcile import local_net_view

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
    if row["status"] != pause_core.STATUS_PAUSED:
        raise AiTradePreflightError("AI_TRADE_NOT_PAUSED", "该分组不处于已暂停状态", 409)
    if pause_core.is_age_exceeded(row["pausedTs"], int(time.time()),
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
    assessment.verdict = pause_core.group_verdict(
        [m["verdict"] for m in assessment.members],
        [m.get("blockedReason") for m in assessment.members])
    return assessment


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
    await _adopt_group(group, assessment)                       # S6：接管连续量
    try:                                                        # S7：起会话（整组回滚）
        for member in group.members:
            await member.session.start()
    except Exception:
        for member in group.members:
            # **先摘掉 W5 回调**：那条回调是给「一个已经在跑的会话自终止」用的，会把契约置
            # `ended`。而续跑失败时契约必须**留在 paused**（S7：可再试）——这些会话根本没起来，
            # 交易所上不可能有它们开出的仓位，没有任何东西需要被收敛。
            member.session.on_stopped = None
            try:
                await member.session.stop("resume_failed")
            except Exception:  # noqa: BLE001 —— 回滚清理失败不掩盖原始异常
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
    """S6：把连续量灌回 executor / decider / session（六项，每项都有一个会被漏掉的坑）。"""
    by_inst = {m["instId"]: m for m in assessment.members}
    contract_members = {str(m.get("instId") or ""): m for m in assessment.row["members"]}
    net_mode = bool(group.members[0].session.executor.settings.ai_trade_net_pnl_enabled) \
        if group.members else True
    for member in group.members:
        session = member.session
        # `started_ts` 取**原分组** createdTs（不是续跑时刻）：取续跑时刻会让窗口对账只覆盖后半段，
        # `coverageMismatch` 恒真。
        session.started_ts = int(assessment.row.get("createdTs") or session.started_ts)
        contract = contract_members.get(member.inst_id) or {}
        session._known_cl_ord_ids = [str(c) for c in (contract.get("knownClOrdIds") or [])]
        continuity = await rebuild_continuity(member.run_id, net_mode=net_mode)
        executor = session.executor
        # D5：走 `restore_trades` 而不是直接赋值——它同时**就地派生**两个计数器并施加同一份封顶。
        # 计数器刻意**不落库**：本模块 `rebuild_continuity` 的不变式原文是「派生量绝不落库：落两份
        # 必然漂移，而这里漂移的是真实资金的盈亏显示」，而 `trades` 本就是从 DB 全量 `Trade` 行重建的
        # （无裁剪），所以恢复后的计数器与暂停前**逐位相同**，零新增持久化键、零老库兼容分支。
        executor.restore_trades(continuity["trades"])
        executor.realized_pnl_usd = continuity["realizedPnlUsd"]
        executor.entry_fee_usd = continuity["entryFeeUsd"]
        session._position_seq = continuity["positionSeq"]
        assessed = by_inst.get(member.inst_id) or {}
        if assessed.get("verdict") == pause_core.VERDICT_CLOSED_OFFLINE:
            # **必须在重算 pnl 之后、接管之前**：它可能补写一条平仓 trade，而 executor 的成交流
            # 已经从 DB 重建完毕；这一笔只影响历史统计与 `offlineGap` 标注，不改接管后的持仓。
            await settle_closed_offline(member, contract, creds=assessment.creds,
                                        cost_basis=_cost_basis(session),
                                        paused_ts=int(assessment.row.get("pausedTs") or 0))
        adopt_position(executor, assessed.get("actual"))
        # W-A · H-3：**仍持仓**的接管。这一类往返在 v1 采不到——`CostRuntime.note_open` 只挂在
        # 开仓成交上，跨进程接管后 `last_settled_trip()` 恒 None ⇒ 该往返只贡献一个 `harvestGap`。
        # 而会被跨进程接管的，恰恰是**那些没能在会话内平掉的**仓位（超时、被套、等反弹），
        # 在净额分布上系统性偏左：整类剔除等于给经验表做了一次幸存者偏差筛选。
        experience = _experience(member)
        if experience is not None:
            experience.adopt_open(_adopted_open(member, assessed.get("actual"),
                                                continuity["entryFeeUsd"]))
        session._last_status_side = executor.projected
        snapshot = await _run_snapshot(member.run_id)
        session.rounds_completed = int(snapshot.get("roundsCompleted") or 0)
        session.decider.seed_from_snapshot(summary=snapshot.get("finalSummary"),
                                           last_round=session.rounds_completed)


def _experience(member):
    """成员会话的经验层门面；未装配 ⇒ `None`。

    **极性说明（本文件的 fail-closed 只管取数，不管旁路观测）**：续跑本身是一次开仓授权，
    所以 S3 的取数一律 fail-closed。但经验采集是**旁路观测**——它缺席的最坏后果是「这一笔往返
    没进经验库」，而把它变成一个能挡下合法续跑的异常，等于让一个统计增强读数决定真实资金能不能
    继续交易。沿用 `experience.runtime.persist_pending` 的 best-effort 姿态。
    """
    return getattr(getattr(member, "session", None), "experience", None)


def _adopted_open(member, actual: dict | None, entry_fee_usd: float) -> AdoptedOpen | None:
    """把接管到的交易所真值装配成经验层的合成开仓断面（W-A · H-3）；已平 / 无仓 ⇒ `None`。

    `entry_fee_usd == 0.0` 一律当作**不可知**而不是「零手续费」：`rebuild_continuity` 是
    `sum(r.fee or 0.0)`，它在结构上分不出这两种情况，而 `trip_ledger` 的约束 2b 要求两者可区分。
    当作不可知的后果是该事件因 `v1:I-7` 被排除在 bps 统计外但**仍然入表**并计进桶的 `excluded`
    ——被看见的缺失，而不是一个编出来的零。
    """
    executor = member.session.executor
    if executor.projected == "flat" or executor.entry_price <= 0 or executor.entry_qty_sz <= 0:
        return None
    return AdoptedOpen(
        seq=int(member.session._position_seq), side=str(executor.projected),
        entry={"px": float(executor.entry_price), "qty": float(executor.entry_qty_sz),
               # 与平仓 trade 的 `ts` 同一时钟（墙钟秒）：交易所 `cTimeS` 是权威开仓时刻。
               "ts": int((actual or {}).get("cTimeS") or 0)},
        open_fee_usd=(float(entry_fee_usd) if float(entry_fee_usd or 0.0) > 0 else None),
        ct_val=float(getattr(executor, "ct_val", 0.0) or 0.0),
        horizon_profile_id=getattr(member.session.horizon_rule, "profile_id", None))


async def settle_closed_offline(member, contract: dict, *, creds, cost_basis: str,
                                paused_ts: int) -> None:
    """`closed_offline` 的盈亏归属（§3.6 / [P1-7]）——本轮**最容易写出假数据**的一处。

    能从 `positions-history` 取到该 `posId` ⇒ 写**一条**平仓 trade；取不到 / 增量不自洽 ⇒
    **不写任何 trade 行**，改在 `metrics.aiStats.offlineGap` 里如实标注「这一笔盈亏未能回读」。

    **绝不写 `pnl=0.0`**：`trades.pnl` 是 `Mapped[float]` 且 `append_live_trade` 会把 `None`
    兜成 `0.0` —— 那是一条「这笔交易不赚不亏」的**假数据**，会污染胜率、验收卡与已实现 edge 校准。
    宁可缺一笔并说明，也不编一个 0。

    口径换算（**绝不直接写 `realizedPnl`**，那是量纲错误）：官方关系式是
    `realizedPnl = pnl + fee + fundingFee + liqPenalty`，即 `realizedPnl` 已扣费且含资金费；
    而本地 `trades.pnl` 的口径由该 run 的 `costBasis` 决定，资金费另有 `funding_ledger` 台账。
    直接照抄会同时踩两个坑：`gross` 时被 `local_net_view` 再减一次腿费、资金费与台账双记。
    """
    # W-A · H-1：合成断面必须建在 `fetch_position_history` **之前**——下面两条出口都要用它，
    # 而本函数跑在 `adopt_position` **之前**（`_adopt_group` 的调用顺序，★ E-5），此刻
    # `executor` 里还没有这笔仓的任何真值。数据源因此只能是 `contract["position"]`，与
    # `_offline_exit_trade` 读的 `openCTime` / `size` **同源**。
    experience = _experience(member)
    entry_fee = float(getattr(member.session.executor, "entry_fee_usd", 0.0) or 0.0)
    if experience is not None:
        experience.adopt_open(synth_from_contract(
            contract, seq=int(member.session._position_seq),
            ct_val=float(getattr(member.session.executor, "ct_val", 0.0) or 0.0),
            open_fee_usd=(entry_fee if entry_fee > 0 else None)))
    expected = contract.get("position") or {}
    pos_id = str(expected.get("posId") or "")
    row = None
    try:
        rows = await fetch_position_history(creds, member.inst_id,
                                            after_ms=max(0, int(paused_ts)) * 1000)
        row = next((r for r in rows if pos_id and str(r.get("posId") or "") == pos_id), None)
    except Exception as exc:  # noqa: BLE001 —— 回读失败只让这一笔缺失，绝不阻断续跑
        logger.warning("ai-trade offline exit readback failed (%s): %s", member.inst_id, exc)
    trade = _offline_exit_trade(row, expected, cost_basis=cost_basis,
                                local_trades=member.session.executor.trades)
    if trade is None:
        await _mark_offline_gap(member.run_id, member.inst_id, has_row=row is not None)
        # W-A · H-2（★ F-6 的正解）：**这条早退分支才是「暂停期离线平仓未能采集」的唯一真实
        # 落点**。`ExperienceCard` 上那句文案在 v1 的后一半恒为 0——产品指名道姓承诺会计的一个
        # 数，从来没有被计过。`has_row` 把「采不到」分成两种真实存在的原因（回读不到 / 增量不自洽）。
        if experience is not None:
            await experience.note_offline_close(None, has_row=row is not None)
        return
    async with get_sessionmaker()() as db:
        from ..db import repositories as repo

        await repo.append_live_trade(db, member.run_id, trade)
    # W-A · H-2：有回执 ⇒ 用合成断面跑一次**既有的** `trip_ledger.settle` ⇒ 事件入表并落盘
    # （`origin="adopted"`）。绝不另写一份 bps 换算（A-12 逐位断言）。
    if experience is not None:
        await experience.note_offline_close(trade, has_row=True)


def _offline_exit_trade(row: dict | None, expected: dict, *, cost_basis: str,
                        local_trades: list[dict]) -> dict | None:
    """交易所持仓历史行 → 一条**增量**平仓 trade；不可用 / 不自洽 ⇒ None（退回诚实缺口）。

    `positions-history` 一行覆盖的是**整个持仓生命周期**（按 `posId` 汇总），不是「离线那一段」。
    若暂停**之前**该持仓已发生过部分平仓并落过本地 `trades` 行，照抄整行会把那部分重复计入。
    故只取增量：用交易所给的**建仓时刻**划段（比 `positionSeq` 可靠——后者在任一次部分平仓时就
    会 +1，根本不与一个 `posId` 的生命周期对齐，见 spec §实施过程发现的方案缺陷）。
    """
    if not row:
        return None
    fee = float(row.get("fee") or 0.0)
    realized = float(row.get("realizedPnl") or 0.0)
    if cost_basis == "gross":
        pnl = realized + fee                         # 把费加回去还原毛额
    else:
        pnl = realized - float(row.get("fundingFee") or 0.0) \
            - float(row.get("liqPenalty") or 0.0)    # 只剥掉本地不入 trades.pnl 的那两项
    open_ts = int(expected.get("openCTime") or expected.get("openWallTs") or 0)
    local = [t for t in (local_trades or [])
             if open_ts > 0 and int(t.get("ts") or 0) >= open_ts]
    pnl -= sum(float(t.get("pnl") or 0.0) for t in local if t.get("side") == "close")
    fee -= sum(float(t.get("fee") or 0.0) for t in local)
    if fee < 0 or (realized and abs(pnl) > abs(realized) * 3):
        return None                                  # 不自洽 ⇒ 宁可缺一笔并说明
    return {"ts": int(row.get("uTime") or 0), "side": "close",
            "price": float(row.get("closeAvgPx") or 0.0),
            "qty": abs(float(expected.get("size") or 0.0)),
            "fee": fee, "pnl": pnl, "positionAfter": 0.0, "reason": "offline_exit",
            "explain": {"kind": "offline_exit", "headline": "离线期间该仓已在交易所侧平掉",
                        "summary": "续跑前从交易所持仓历史回读的一笔平仓",
                        "meta": {"source": "ai", "exitSource": "offline",
                                 "posId": str(row.get("posId") or ""),
                                 "offlineFundingUsd": float(row.get("fundingFee") or 0.0),
                                 "offlineLiqPenaltyUsd": float(row.get("liqPenalty") or 0.0)}}}


async def _mark_offline_gap(run_id: int, inst_id: str, *, has_row: bool) -> None:
    """把「这一段统计缺一笔」如实写进 `metrics.aiStats.offlineGap`（绝不补一个 0）。"""
    from ..db import repositories as repo

    metrics = await _run_snapshot(run_id)
    stats = metrics.get("aiStats")
    metrics["aiStats"] = stats if isinstance(stats, dict) else {}
    metrics["aiStats"]["offlineGap"] = {
        "instId": inst_id, "verdict": "closed_offline", "pnlUnknown": True,
        "reason": "inconsistent_increment" if has_row else "not_found"}
    async with get_sessionmaker()() as db:
        # 此刻 run 仍是 `paused`（S7 收尾才拨回 `running`），故这里如实带上它，绝不顺手改状态。
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


async def rebuild_continuity(run_id: int, *, net_mode: bool) -> dict:
    """从 `trades` 重算派生量（§3.4.3 右列）：executor 成交流 / 已实现盈亏 / 持仓序号 / 开仓腿费。

    **派生量绝不落库**：落两份必然漂移，而这里漂移的是真实资金的盈亏显示。`realized_pnl_usd`
    的 `net_mode` 口径与 `reconcile.local_net_view` **同一条规则**（两侧不可各写一份）。
    """
    async with get_sessionmaker()() as db:
        rows = (await db.execute(
            select(Trade).where(Trade.run_id == run_id)
            .order_by(Trade.ts.asc(), Trade.id.asc()))).scalars().all()
    trades = [{"ts": r.ts, "side": r.side, "price": r.price, "qty": r.qty,
               "fee": r.fee or 0.0, "pnl": r.pnl or 0.0,
               "positionAfter": r.position_after, "reason": r.reason}
              for r in rows]
    entry_fee = 0.0
    for trade in trades:      # 只累计**当前**未平持仓周期内的开仓腿费（平仓即清零）
        entry_fee = 0.0 if trade["side"] == "close" else entry_fee + float(trade["fee"] or 0.0)
    return {"trades": trades,
            "realizedPnlUsd": local_net_view(trades, net_mode=net_mode)["netUsd"],
            "positionSeq": sum(1 for t in trades if t["side"] == "close"),
            "entryFeeUsd": entry_fee}


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
