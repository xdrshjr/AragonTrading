"""LiveAutoManager —— 实时自动交易「单活跃分组」注册表（watchlist-auto-trade-group §2.4 / §2.6）。

职责：两段式启动前置门禁（安全关键，绝不假启动）、单活跃分组独占（一次至多一个 group，含 1..N 个
会话）、lifecycle（start_group/stop_group/stop_member/group_status/get/shutdown）。真实下单一律经
`LiveExecutor` → `okx/trading.py` 服务层，本层只做编排与守门。`LiveExecutor` / `LiveAutoSession`
**零改动**（每会话仍只管一个标的）。单标的旧路径（`start`/`stop`/`active`/`status_row`）降为 1 成员、
100% 分配分组的兼容 wrapper，外部契约不破。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass

from ..config import Settings
from ..db import repositories as repo
from ..db.session import get_sessionmaker
from ..okx.accounts import AccountCredentials
from ..okx.order_builder import snap_size
from ..okx.trading import fetch_live_positions, fetch_trading_config
from .executor import LiveExecutor
from .session import LiveAutoSession

logger = logging.getLogger(__name__)

# 兼容单标的旧路径：1 成员分组前置失败时，把分组级语义码回退为历史单会话状态码（旧端点契约不破）。
_SINGLE_PREFLIGHT_STATUS = {
    "LIVE_INSTRUMENT_UNSUPPORTED": 400,
    "LIVE_AUTO_PREEXISTING_POSITION": 409,
    "LIVE_AUTO_MEMBER_BUDGET_TOO_SMALL": 400,
}


class LiveAutoPreflightError(Exception):
    """启动前置门禁不通过（携稳定语义码与 HTTP 状态，API 层据此回结构化错误）。"""

    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


class LiveAutoSessionLimitError(LiveAutoPreflightError):
    """已有活跃自动分组（单活跃分组独占）。"""


class LiveAutoAllocationError(LiveAutoPreflightError):
    """分组百分比非法（空成员 / 越界 / 和超 100）。"""


class LiveAutoGroupTooLargeError(LiveAutoPreflightError):
    """分组标的数超 live_auto_max_group_symbols 硬上限。"""


class LiveAutoGroupPreflightError(LiveAutoPreflightError):
    """整组前置门禁失败（携逐标的 `.members` 明细，分组路由须透传，P1-4）。"""

    def __init__(self, members: list[dict],
                 message: str = "部分标的无法启动自动交易", status: int = 409):
        self.members = members
        super().__init__("LIVE_AUTO_GROUP_PREFLIGHT", message, status)


def _creds(account_row: repo.OkxAccount) -> AccountCredentials:
    return AccountCredentials(api_key=account_row.api_key, secret_key=account_row.secret_key,
                              passphrase=account_row.passphrase)


@dataclass
class GroupMemberInput:
    """启动请求中的单成员：标的 + 可用仓位百分比（(0,100]，整组 Σ ≤ 100）。"""

    inst_id: str
    allocation_pct: float


@dataclass
class GroupStartRequest:
    """`start_group` 聚合形参（P2-2：避免位置堆参，满足 CLAUDE.md ≤5 形参）。

    `budget_cap_frac` / `default_budget_frac` / `allocation_mode` 由各入口解析后填入
    （auto-trade-budget-and-allocation-settings RN-1）：分组路由按 DB maxBudgetPct 夹分组天花板，
    单标的兼容 wrapper 传旧 0.05 / 0.01 → 单标的行为逐位不变。默认值取分组语义（上限 100% / 留空=上限 /
    even），使直接构造的请求与分组面板默认一致。
    """

    strategy_spec: object
    bar: str
    members_in: list[GroupMemberInput]
    account_row: repo.OkxAccount
    settings: Settings
    total_capital_usd: float | None = None
    leverage: float | None = None
    take_profit_usd: float | None = None
    stop_loss_usd: float | None = None
    priming: bool = True
    priming_bars: int = 300
    budget_cap_frac: float = 1.0       # 预算硬上限分数（avail×该值），由入口解析
    default_budget_frac: float = 1.0   # 留空默认分数（avail×该值），分组路径 = 上限本身
    allocation_mode: str = "even"      # even | max_ratio（§2.3）


@dataclass
class LiveAutoGroupMember:
    """活跃分组内的单成员（会话 + 落地预算 / 百分比 / Run）。"""

    inst_id: str
    allocation_pct: float
    budget_usd: float
    session: LiveAutoSession
    run_id: int


@dataclass
class LiveAutoGroup:
    """进程内当前活跃分组（一次至多一个）。"""

    group_id: str
    strategy_id: int
    strategy_name: str
    strategy_kind: str
    bar: str
    total_budget_usd: float
    leverage: float
    created_ts: int
    members: list[LiveAutoGroupMember]


@dataclass
class _MemberPlan:
    """通过第一阶段门禁的单成员落地参数（待第二阶段建会话）。"""

    inst_id: str
    allocation_pct: float
    budget_usd: float
    config: dict


@dataclass
class _GroupBuildCtx:
    """第二阶段建会话的共享上下文（避免 `_build_member` 形参堆叠，P2-2）。"""

    req: GroupStartRequest
    creds: AccountCredentials
    lever: float
    sum_pct: float
    total_budget_usd: float
    group_id: str


class LiveAutoManager:
    """进程内单活跃分组注册表（真实资金，一次至多一个 group，含 1..N 个会话）。"""

    def __init__(self):
        self._group: LiveAutoGroup | None = None
        # start 关键区互斥锁，消除「容量检查 → 置位 self._group」之间的 TOCTOU（单活跃分组）。
        self._start_lock = asyncio.Lock()

    # ---------- 分组启动（两段式：全量门禁 → 建会话 → 起会话 / 回滚）----------
    async def start_group(self, req: GroupStartRequest) -> LiveAutoGroup:
        """§2.4：双闸 → 容量 → 百分比校验 → 账户级数据各取一次 → 两段式门禁 → 整组回滚启动。"""
        self._require_enabled(req.settings)
        async with self._start_lock:
            self._require_capacity()
            members = self._validate_members(req.members_in, req.settings, req.allocation_mode)
            creds = _creds(req.account_row)
            avail, positions, configs = await self._gather_context(creds, req, members)
            total = self._resolve_budget_group(
                req.total_capital_usd, avail, req.budget_cap_frac, req.default_budget_frac)
            lever = req.leverage or req.settings.live_default_leverage
            plans = self._preflight_group(members, configs, positions, total, lever)
            group = await self._build_group(req, creds, plans, total, lever)
            self._group = group
            return await self._launch_group_or_rollback(group)

    def _validate_members(self, members_in: list[GroupMemberInput],
                          settings: Settings,
                          mode: str = "even") -> list[GroupMemberInput]:
        """容量 / 百分比 / 去重边界校验（边界一次性校验，进入内部即信任，§五）。

        `mode == "even"` 强制 Σ allocationPct ≤ 100；`mode == "max_ratio"` 跳过 Σ 校验
        （各标的对总预算的独立上限，可超 100%，§2.3）。其余闸（非空 / 去重 / 超标的数上限 /
        单成员 ∈ (0,100]）两模式一致。
        """
        if not members_in:
            raise LiveAutoAllocationError("LIVE_AUTO_GROUP_EMPTY", "自动交易分组为空", 400)
        seen: set[str] = set()
        unique = [m for m in members_in if not (m.inst_id in seen or seen.add(m.inst_id))]
        if len(unique) > settings.live_auto_max_group_symbols:
            raise LiveAutoGroupTooLargeError(
                "LIVE_AUTO_GROUP_TOO_LARGE",
                f"分组标的数超上限（≤ {settings.live_auto_max_group_symbols}）", 400)
        if any(not (0 < m.allocation_pct <= 100) for m in unique):
            raise LiveAutoAllocationError(
                "LIVE_AUTO_ALLOCATION_INVALID", "每个标的可用仓位百分比须在 (0, 100] 之间", 400)
        if mode == "even" and sum(m.allocation_pct for m in unique) > 100 + 1e-9:
            raise LiveAutoAllocationError(
                "LIVE_AUTO_ALLOCATION_INVALID", "各标的可用仓位百分比之和不可超过 100%", 400)
        return unique

    async def _gather_context(self, creds: AccountCredentials, req: GroupStartRequest,
                              members: list[GroupMemberInput]) -> tuple:
        """P1-3：账户可用 USDT 与全账户持仓**整组各取一次**；逐成员仅取该标的 trading_config。

        启动瞬间的签名私有请求按 `live_auto_start_concurrency`（默认 2）限流排队，避免一次性把
        N×2 发 account/config + account/balance 打爆 OKX 私有限频触发 429（见 docs/diagnoses/
        live-auto-multi-symbol-rate-limit-429）。`fetch_live_positions` 纳入同一信号量预算。
        """
        sem = asyncio.Semaphore(max(1, req.settings.live_auto_start_concurrency))

        async def _bounded_config(inst_id: str) -> dict:
            async with sem:
                return await fetch_trading_config(
                    creds, req.account_row.id, inst_id, settings=req.settings)

        configs = await asyncio.gather(*[_bounded_config(m.inst_id) for m in members])
        async with sem:
            positions = await fetch_live_positions(creds)
        config_map = {m.inst_id: cfg for m, cfg in zip(members, configs)}
        avail = next((cfg.get("availUsdt") or 0.0 for cfg in configs), 0.0)
        return avail, positions, config_map

    def _preflight_group(self, members: list[GroupMemberInput], configs: dict,
                         positions: list[dict], total: float, lever: float) -> list[_MemberPlan]:
        """第一阶段·全量前置门禁（**不下任何单**）：任一成员失败即整组拒绝并携明细（P1-4）。"""
        failures: list[dict] = []
        plans: list[_MemberPlan] = []
        for m in members:
            cfg = configs[m.inst_id]
            budget_i = total * m.allocation_pct / 100.0
            fail = self._member_preflight_fail(m.inst_id, cfg, positions, budget_i, lever)
            if fail is not None:
                failures.append(fail)
            else:
                plans.append(_MemberPlan(m.inst_id, m.allocation_pct, budget_i, cfg))
        if failures:
            raise LiveAutoGroupPreflightError(failures)
        return plans

    def _member_preflight_fail(self, inst_id: str, cfg: dict, positions: list[dict],
                               budget_i: float, lever: float) -> dict | None:
        """单成员门禁：不支持 / 已有持仓 / 预算过小 → 返回明细；通过 → None。"""
        if not cfg.get("instrumentSupported"):
            return {"instId": inst_id, "code": "LIVE_INSTRUMENT_UNSUPPORTED",
                    "message": "仅支持 USDT 本位永续 / 交割合约自动交易"}
        if self._has_position(positions, inst_id):
            return {"instId": inst_id, "code": "LIVE_AUTO_PREEXISTING_POSITION",
                    "message": "该标的已有持仓，自动交易拒绝启动（请先平仓或换标的）"}
        if self._budget_too_small(cfg, budget_i, lever):
            return {"instId": inst_id, "code": "LIVE_AUTO_MEMBER_BUDGET_TOO_SMALL",
                    "message": "分配预算不足最小下单量，请提高总预算或该标的百分比"}
        return None

    @staticmethod
    def _budget_too_small(cfg: dict, budget_i: float, lever: float) -> bool:
        """镜像 LiveExecutor._compute_sz：可下张数 < minSz 即判过小；行情缺失则不拦（运行期再守）。"""
        meta = cfg.get("instMeta") or {}
        ct_val = meta.get("ctVal") or 0.0
        min_sz = meta.get("minSz") or 0.0
        lot_sz = meta.get("lotSz") or 0.0
        last_px = cfg.get("lastPx") or 0.0
        if ct_val <= 0 or last_px <= 0 or min_sz <= 0:
            return False
        raw = (budget_i * lever) / (ct_val * last_px)
        return snap_size(raw, lot_sz) < min_sz

    async def _build_group(self, req: GroupStartRequest, creds: AccountCredentials,
                           plans: list[_MemberPlan], total: float,
                           lever: float) -> LiveAutoGroup:
        """第二阶段·建会话（**不启动**）：逐成员建 Run + Executor + Session（止盈止损按比例分摊）。"""
        spec = req.strategy_spec
        ctx = _GroupBuildCtx(req=req, creds=creds, lever=lever,
                             sum_pct=sum(p.allocation_pct for p in plans), total_budget_usd=total,
                             group_id=f"liveautogrp_{uuid.uuid4().hex}")
        members = [await self._build_member(ctx, p) for p in plans]
        return LiveAutoGroup(
            group_id=ctx.group_id, strategy_id=spec.strategy_id, strategy_name=spec.strategy_name,
            strategy_kind=spec.strategy_kind, bar=req.bar, total_budget_usd=total,
            leverage=lever, created_ts=int(time.time()), members=members)

    async def _build_member(self, ctx: _GroupBuildCtx, plan: _MemberPlan) -> LiveAutoGroupMember:
        """建单成员会话：Executor（按 budget_i）+ Run（含 groupId/allocationPct）+ Session（tp_i/sl_i）。"""
        req, spec = ctx.req, ctx.req.strategy_spec
        executor = self._build_executor(ctx.creds, req.account_row.id, plan.inst_id, plan.config,
                                        plan.budget_usd, ctx.lever, spec.strategy.risk_params(),
                                        req.settings)
        tp_i = self._scale_threshold(req.take_profit_usd, plan.allocation_pct, ctx.sum_pct)
        sl_i = self._scale_threshold(req.stop_loss_usd, plan.allocation_pct, ctx.sum_pct)
        run_id = await self._create_group_run(ctx, plan, tp_i, sl_i)
        session = LiveAutoSession(
            f"liveauto_{uuid.uuid4().hex}", run_id, spec.strategy, plan.inst_id, req.bar,
            executor, take_profit_usd=tp_i, stop_loss_usd=sl_i, priming=req.priming,
            priming_bars=req.priming_bars, strategy_name=spec.strategy_name,
            strategy_kind=spec.strategy_kind, strategy_id=spec.strategy_id)
        return LiveAutoGroupMember(inst_id=plan.inst_id, allocation_pct=plan.allocation_pct,
                                   budget_usd=plan.budget_usd, session=session, run_id=run_id)

    async def _launch_group_or_rollback(self, group: LiveAutoGroup) -> LiveAutoGroup:
        """第二阶段·起会话；任一失败 → 回滚**全部**成员会话 + 清组，不掩盖原异常（镜像 paper start_batch）。"""
        try:
            for member in group.members:
                await member.session.start()
        except Exception:
            # 回滚须覆盖**所有**成员（非仅已起的）：_build_group 已为每个成员建了 status="running" 的
            # Run，session.stop 幂等且对未起会话安全（state 初始即 "running" → 落 Run 终态并清理 WS/盯市），
            # 杜绝起失败成员及其之后成员的 Run 永久滞留 "running" 成孤儿（兼容旧单标的路径同此行为）。
            for member in group.members:
                try:
                    await member.session.stop("start_failed")
                except Exception:  # noqa: BLE001 —— 回滚清理失败不掩盖原始启动异常
                    pass
            self._group = None
            raise
        return group

    # ---------- 分组停止 ----------
    async def stop_group(self, group_id: str, reason: str = "user_stop") -> dict | None:
        """停所有成员（各自平自管仓、落 Run 终态），聚合每成员平仓回执；全部停止后清组。"""
        group = self._group
        if group is None or group.group_id != group_id:
            return None
        rows = []
        for member in group.members:
            flat = await self._stop_session(member.session, reason)
            rows.append({"instId": member.inst_id, "sessionId": member.session.session_id,
                         "runId": member.run_id, "flattened": flat})
        self._group = None
        return {"groupId": group_id, "stopReason": reason, "members": rows}

    async def stop_member(self, group_id: str, inst_id: str) -> dict | None:
        """仅停某成员（其余续跑，P2-3：保留为 stopped、不支持单成员重启）；停后无运行成员则清组。"""
        group = self._group
        if group is None or group.group_id != group_id:
            return None
        member = next((m for m in group.members if m.inst_id == inst_id), None)
        if member is None:
            return None
        flat = await self._stop_session(member.session, "user_stop")
        if not any(m.session.state == "running" for m in group.members):
            self._group = None
        return {"groupId": group_id, "instId": inst_id, "flattened": flat}

    async def _stop_session(self, session: LiveAutoSession, reason: str) -> dict:
        flat = await session.stop(reason)
        if not flat.get("ok"):   # H4：平仓失败不静默——日志留痕（API 仍回 flattened 供 UI 强提示）
            logger.warning("live-auto group stop flatten failed: session=%s code=%s residual=%s",
                           session.session_id, flat.get("code"), flat.get("residual"))
        return flat

    # ---------- 只读视图 / WS 路由 ----------
    def group_status(self) -> dict | None:
        """活跃分组只读视图（前端重载后据此重连各成员 WS）；无活跃分组→ None。"""
        group = self._group
        if group is None:
            return None
        return {"groupId": group.group_id, "strategyId": group.strategy_id,
                "strategyName": group.strategy_name, "strategyKind": group.strategy_kind,
                "bar": group.bar, "totalBudgetUsd": group.total_budget_usd,
                "leverage": group.leverage, "createdTs": group.created_ts,
                "members": [self._member_row(m) for m in group.members]}

    @staticmethod
    def _member_row(member: LiveAutoGroupMember) -> dict:
        session = member.session
        executor = session.executor
        return {"instId": member.inst_id, "sessionId": session.session_id, "runId": member.run_id,
                "allocationPct": member.allocation_pct, "budgetUsd": member.budget_usd,
                "state": session.state, "positionSide": getattr(executor, "projected", None),
                "sessionPnlUsd": getattr(executor, "session_pnl_usd", 0.0),
                "degraded": getattr(executor, "degraded", False),
                "flattenFailed": getattr(session, "flatten_failed", False)}

    def get(self, session_id: str) -> LiveAutoSession | None:
        """跨所有成员按 session_id 查会话（WS 路由用）。"""
        group = self._group
        if group is None:
            return None
        return next((m.session for m in group.members if m.session.session_id == session_id), None)

    async def shutdown(self) -> None:
        """停所有成员会话（进程退出场景，沿用 H4 不静默吞平仓失败）。"""
        group = self._group
        if group is not None:
            for member in group.members:
                if member.session.state == "running":
                    await self._stop_session(member.session, "shutdown")
        self._group = None

    # ---------- 兼容层：单标的旧路径（1 成员、100% 分配分组）----------
    async def start(self, *, strategy_spec, inst_id: str, bar: str,
                    account_row: repo.OkxAccount, settings: Settings,
                    capital_usd: float | None = None, leverage: float | None = None,
                    take_profit_usd: float | None = None, stop_loss_usd: float | None = None,
                    priming: bool = True, priming_bars: int = 300) -> LiveAutoSession:
        """兼容 wrapper：1 成员、100% 分配的分组（旧 /live-auto/start 契约不破，含具体语义码回退）。"""
        req = GroupStartRequest(
            strategy_spec=strategy_spec, bar=bar,
            members_in=[GroupMemberInput(inst_id=inst_id, allocation_pct=100.0)],
            account_row=account_row, settings=settings, total_capital_usd=capital_usd,
            leverage=leverage, take_profit_usd=take_profit_usd, stop_loss_usd=stop_loss_usd,
            priming=priming, priming_bars=priming_bars,
            # RN-1：单标的旧路径绝对天花板 5% / 留空默认 1%（**不**引用分组天花板与 maxBudgetPct）。
            budget_cap_frac=settings.live_auto_max_capital_frac,
            default_budget_frac=settings.live_auto_default_capital_frac,
            allocation_mode="even")
        try:
            group = await self.start_group(req)
        except LiveAutoGroupPreflightError as exc:
            detail = exc.members[0] if exc.members else {}
            code = detail.get("code", exc.code)
            raise LiveAutoPreflightError(
                code, detail.get("message", exc.message),
                _SINGLE_PREFLIGHT_STATUS.get(code, exc.status)) from exc
        return group.members[0].session

    async def stop(self, session_id: str, reason: str = "user_stop") -> tuple | None:
        """兼容 wrapper：按 session_id 停单会话；停后无运行成员则清组（旧 /live-auto/{sid}/stop 不破）。"""
        session = self.get(session_id)
        if session is None:
            return None
        flat = await self._stop_session(session, reason)
        group = self._group
        if group is not None and not any(m.session.state == "running" for m in group.members):
            self._group = None
        return session, flat

    def active(self) -> LiveAutoSession | None:
        """兼容：恰一个运行成员时返回之（旧单会话语义），否则 None。"""
        group = self._group
        if group is None:
            return None
        running = [m.session for m in group.members if m.session.state == "running"]
        return running[0] if len(running) == 1 else None

    def status_row(self) -> dict | None:
        """兼容：分组恰 1 成员且运行时回旧单会话形态，否则 None（旧 GET /live-auto/status 不破）。"""
        group = self._group
        if group is None or len(group.members) != 1:
            return None
        session = group.members[0].session
        return session.info() if session.state == "running" else None

    # ---------- 前置门禁辅助 ----------
    def _require_enabled(self, settings: Settings) -> None:
        """R1：自动化总闸与底层写链路总闸须同时为 True（不依赖『服务层内已强制』）。"""
        if not (settings.live_auto_trading_enabled and settings.live_trading_enabled):
            raise LiveAutoPreflightError(
                "LIVE_AUTO_TRADING_DISABLED", "实时自动交易未启用", 403)

    def _require_capacity(self) -> None:
        group = self._group
        if group is not None and any(m.session.state == "running" for m in group.members):
            raise LiveAutoSessionLimitError(
                "LIVE_AUTO_SESSION_LIMIT", "已有活跃自动交易分组（单活跃分组独占，请先停止）", 409)

    @staticmethod
    def _has_position(positions: list[dict], inst_id: str) -> bool:
        return any(str(p.get("instId")) == inst_id and abs(p.get("pos") or 0.0) > 0
                   for p in positions)

    @staticmethod
    def _resolve_budget_group(capital_usd: float | None, avail: float,
                              cap_frac: float, default_frac: float) -> float:
        """留空→ avail×default_frac；非空→ 夹到 ≤ avail×cap_frac（RN-1：两路径共用，分数由各入口解析）。

        分组入口传 cap_frac=default_frac=min(maxBudgetPct/100, 分组天花板)（留空=上限、不随成员数缩放）；
        单标的兼容 wrapper 传旧 0.05 / 0.01（行为逐位不变）。
        """
        hard_cap = avail * cap_frac
        budget = capital_usd if (capital_usd and capital_usd > 0) else avail * default_frac
        return min(budget, hard_cap) if hard_cap > 0 else budget

    @staticmethod
    def _scale_threshold(total_amount: float | None, pct: float, sum_pct: float) -> float | None:
        """P1-2：总止盈/止损按 pct/Σpct 归一分摊（Σpct>0 必成立），各会话阈值之和恒等于总额。"""
        if total_amount is None or sum_pct <= 0:
            return None
        return total_amount * pct / sum_pct

    @staticmethod
    def _build_executor(creds: AccountCredentials, account_id: int, inst_id: str, config: dict,
                        budget: float, lever: float, risk, settings: Settings) -> LiveExecutor:
        meta = config.get("instMeta") or {}
        return LiveExecutor(
            creds=creds, account_id=account_id, inst_id=inst_id,
            td_mode=settings.live_default_td_mode,
            pos_mode=config.get("posMode") or "long_short_mode",
            leverage=lever, budget_usd=budget, risk=risk,
            ct_val=meta.get("ctVal") or 0.0, lot_sz=meta.get("lotSz") or 0.0,
            min_sz=meta.get("minSz") or 0.0, settings=settings)

    @staticmethod
    async def _create_group_run(ctx: _GroupBuildCtx, plan: _MemberPlan,
                                take_profit_usd: float | None,
                                stop_loss_usd: float | None) -> int:
        spec = ctx.req.strategy_spec
        sm = get_sessionmaker()
        async with sm() as s:
            run = await repo.create_run(
                s, strategy_id=spec.strategy_id, inst_id=plan.inst_id, bar=ctx.req.bar,
                mode="live-auto",
                config={"mode": "live-auto", "strategyId": spec.strategy_id,
                        "strategyName": spec.strategy_name, "budgetUsd": plan.budget_usd,
                        "leverage": ctx.lever, "takeProfitUsd": take_profit_usd,
                        "stopLossUsd": stop_loss_usd, "groupId": ctx.group_id,
                        "allocationPct": plan.allocation_pct,
                        "allocationMode": ctx.req.allocation_mode,
                        "totalBudgetUsd": ctx.total_budget_usd},
                status="running")
            return run.id


_manager: LiveAutoManager | None = None


def get_live_auto_manager() -> LiveAutoManager:
    global _manager
    if _manager is None:
        _manager = LiveAutoManager()
    return _manager
